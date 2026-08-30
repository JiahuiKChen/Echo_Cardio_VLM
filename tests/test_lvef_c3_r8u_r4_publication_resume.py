#!/usr/bin/env python3
"""Focused dependency-light R8U-R4 portable replay/publication proofs."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import inspect
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import traceback
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finalize_lvef_c3_production as finalizer
import lvef_c3_r8r_recovery_continuation as controller


SHA = "a" * 64
OTHER_SHA = "b" * 64
COMMIT = "c" * 40
R3_SEAL_SHA256 = (
    "cfb0b19044db53742c1fd6121f8d33b567f6a62c2661050cf4df2cc4e6f2cbf2"
)


def _code(exc: BaseException) -> str:
    return str(getattr(exc, "code", exc))


def _expect_code(action: Callable[[], Any], expected: str) -> None:
    try:
        action()
    except Exception as exc:
        assert _code(exc) == expected, (_code(exc), expected)
    else:
        raise AssertionError(f"expected {expected}")


def _projection_value(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    projected = getattr(value, "value", None)
    assert isinstance(projected, Mapping), type(value)
    return dict(projected)


def _control_files() -> frozenset[str]:
    return frozenset(
        getattr(
            controller,
            "R8U_R4_CANDIDATE_CONTROL_FILES",
            controller.R8U_R3_CANDIDATE_CONTROL_FILES,
        )
    )


def _candidate_fixture(root: Path) -> tuple[PurePosixPath, Path]:
    root.mkdir(mode=0o700)
    for index, name in enumerate(sorted(_control_files())):
        path = root / name
        path.write_bytes(f"control-{index}\n".encode("ascii"))
        path.chmod(0o600)
    clip_key = hashlib.sha256(b"r8u-r4-synthetic-clip").hexdigest()
    relative = PurePosixPath("clips") / "clips" / clip_key[:2] / f"{clip_key}.npz"
    npz = root / relative
    npz.parent.mkdir(parents=True, mode=0o700)
    current = npz.parent
    while current != root.parent:
        current.chmod(0o700)
        if current == root:
            break
        current = current.parent
    npz.write_bytes(b"opaque-npz-body")
    npz.chmod(0o600)
    return relative, npz


@contextmanager
def _one_candidate() -> Iterator[tuple[Path, PurePosixPath, Path]]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "dicom_extraction"
        relative, npz = _candidate_fixture(root)
        with mock.patch.object(
            controller, "R8U_R4_CANDIDATE_NPZ_FILES", 1, create=True
        ):
            yield root, relative, npz


@contextmanager
def _forbid_scientific_body_opens() -> Iterator[None]:
    real_os_open = os.open
    real_path_open = Path.open

    def guarded_os_open(path: Any, *args: Any, **kwargs: Any) -> int:
        if isinstance(path, (str, os.PathLike)) and str(path).lower().endswith(
            (".npz", ".dcm")
        ):
            raise AssertionError("R8U-R4 replay opened a scientific body")
        return real_os_open(path, *args, **kwargs)

    def guarded_path_open(path: Path, *args: Any, **kwargs: Any):
        if path.suffix.lower() in {".npz", ".dcm"}:
            raise AssertionError("R8U-R4 replay opened a scientific body")
        return real_path_open(path, *args, **kwargs)

    with (
        mock.patch.object(controller.os, "open", side_effect=guarded_os_open),
        mock.patch.object(Path, "open", guarded_path_open),
    ):
        yield


def _portable_authority(seed: str = "a") -> dict[str, Any]:
    digest = hashlib.sha256(seed.encode("ascii")).hexdigest()
    value: dict[str, Any] = {
        "stage_completion_receipt_sha256": digest,
        "extraction_manifest_sha256": digest,
        "dicom_audit_sha256": digest,
        "extraction_summary_sha256": digest,
        "technical_disposition_manifest_sha256": digest,
        "candidate_npz_manifest_projection_sha256": digest,
        "candidate_regular_files": 10_192,
        "candidate_npz_files": 10_187,
        "candidate_total_bytes": 48_765_432_100,
        "candidate_npz_bytes": 48_765_400_000,
        "candidate_relative_file_path_set_sha256": digest,
        "candidate_relative_npz_path_set_sha256": digest,
        "candidate_relative_file_portable_projection_sha256": digest,
        "candidate_relative_directory_path_set_sha256": digest,
        "candidate_relative_directory_portable_projection_sha256": digest,
        "candidate_root_portable_identity_sha256": digest,
        "canonical_stage_event_authority_sha256": digest,
        "input_manifest_sha256": digest,
        "symlink_count": 0,
        "nonregular_count": 0,
        "n_selected_studies": 250,
        "n_source_objects": 18_677,
        "source_bytes": 66_687_050_120,
        "n_readable": 18_677,
        "n_unreadable": 0,
        "n_multiframe_candidates": 10_187,
        "n_single_frame": 8_490,
        "n_pixel_decode_failures": 0,
        "n_successfully_extracted_cines": 10_187,
        "n_object_technical_dispositions": 0,
        "n_blocking_failures": 0,
        "n_ordinary_preprocessing_path": 10_187,
        "n_spatial_fallback_preprocessing_path": 0,
        "n_temporal_fallback_preprocessing_path": 0,
        "n_spatial_temporal_fallback_preprocessing_path": 0,
        "object_substitution_count": 0,
        "extraction_status": "PASS_EXTRACTION_ALL_OBJECTS_EMBEDDABLE",
    }
    return value


def _with_locality(value: Mapping[str, Any], seed: str) -> dict[str, Any]:
    result = dict(value)
    for field in (
        "source_parent_identity_sha256",
        "target_parent_identity_sha256",
        "source_mount_identity_sha256",
        "target_mount_identity_sha256",
        "source_parent_st_dev_inode_authority",
        "target_parent_st_dev_inode_authority",
        "mount_id",
        "mount_source_identity",
        "candidate_root_identity_sha256",
    ):
        result[field] = hashlib.sha256(f"{seed}:{field}".encode()).hexdigest()
    return result


def _comparison_failure(
    sealed: Mapping[str, Any], observed: Mapping[str, Any], expected: str
) -> None:
    value = controller._r8u_r4_compare_candidate_projection(sealed, observed)
    assert isinstance(value, Mapping)
    assert value.get("portable_candidate_fields_equal") is False
    _expect_code(
        lambda: controller._r8u_r4_raise_portable_comparison(value), expected
    )


def test_r4_controller_api_and_role_specific_errors_are_closed() -> None:
    functions = (
        "_r8u_r4_portable_metadata_projection",
        "_r8u_r4_compare_candidate_projection",
        "_r8u_r4_candidate_replay_diagnosis",
        "_r8u_r4_write_diagnosis_no_clobber",
        "_r8u_r4_live_publication_locality",
        "_r8u_r4_publish_candidate",
        "submit_r8u_r4_batch16_publication_resume",
        "run_r8u_r4_batch16_publication_resume",
        "_current_r8u_r4_implementation_commit",
        "validate_r8u_r4_resume_terminal",
        "validate_r8u_r4_frozen_partial_evidence",
        "validate_r8u_r4_continuation_worker_submission",
    )
    for name in functions:
        assert callable(getattr(controller, name))
    expected_errors = frozenset(
        {
            "R8U_PORTABLE_CANDIDATE_CONTROL_HASH_MISMATCH",
            "R8U_PORTABLE_CANDIDATE_PATH_SET_MISMATCH",
            "R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH",
            "R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH",
            "R8U_LIVE_PUBLICATION_SOURCE_INVALID",
            "R8U_LIVE_PUBLICATION_TARGET_INVALID",
            "R8U_LIVE_PUBLICATION_CROSS_MOUNT",
            "R8U_LIVE_PUBLICATION_PARENT_CHANGED",
            "R8U_LIVE_PUBLICATION_CLAIM_INVALID",
        }
    )
    assert controller.R8U_R4_ROLE_SPECIFIC_ERRORS == expected_errors
    for function_name in functions[:6]:
        assert "CANDIDATE_FAILURE_UNRESOLVED" not in inspect.getsource(
            getattr(controller, function_name)
        )


def test_portable_projection_has_only_portable_identity_and_opens_no_body() -> None:
    with _one_candidate() as (root, relative, _npz), _forbid_scientific_body_opens():
        projected = controller._r8u_r4_portable_metadata_projection(
            root, expected_npz_paths=frozenset({relative})
        )
    value = _projection_value(projected)
    assert set(value) == controller.R8U_R4_PORTABLE_METADATA_FIELDS
    assert (
        controller.R8U_R4_PORTABLE_AUTHORITY_KEYS
        & controller.R8U_R4_NODE_LOCAL_DIAGNOSTIC_FIELDS
    ) == frozenset()
    assert value["candidate_npz_files"] == 1
    forbidden_fields = {
        "st_dev", "inode", "mount_id", "mount_source_identity", "hostname",
        "ctime", "mtime", "source_parent_identity_sha256",
        "target_parent_identity_sha256", "source_mount_identity_sha256",
        "target_mount_identity_sha256", "directory_link_count_projection",
        "candidate_root_identity_sha256",
    }
    assert set(value).isdisjoint(forbidden_fields)


def test_portable_replay_accepts_different_device_parent_inode_and_mount_source() -> None:
    portable = _portable_authority()
    cases = (
        "source_parent_identity_sha256",
        "target_parent_identity_sha256",
        "source_mount_identity_sha256",
        "target_mount_identity_sha256",
        "source_parent_st_dev_inode_authority",
        "target_parent_st_dev_inode_authority",
        "mount_id",
        "mount_source_identity",
        "candidate_root_identity_sha256",
    )
    for field in cases:
        sealed = _with_locality(portable, "login")
        observed = dict(sealed)
        observed[field] = OTHER_SHA
        result = controller._r8u_r4_compare_candidate_projection(sealed, observed)
        assert result["portable_candidate_fields_equal"] is True
        assert result["node_local_only_differences"] is True
        assert field in result["differing_fields"]


def test_historical_unrecorded_portable_projection_is_diagnostic_not_authority() -> None:
    sealed = _portable_authority()
    field = "candidate_relative_directory_portable_projection_sha256"
    sealed.pop(field)
    observed = _portable_authority()
    result = controller._r8u_r4_compare_candidate_projection(sealed, observed)
    assert result["portable_candidate_fields_equal"] is True
    assert result["candidate_path_set_equal"] is True
    assert result["candidate_count_and_bytes_equal"] is True
    diagnosis = controller._r8u_r4_candidate_replay_diagnosis(
        comparison=result,
        candidate_path_set_equal=True,
        candidate_count_and_bytes_equal=True,
    )
    assert diagnosis["first_differing_field"] == "NOT_PERSISTED_BY_FAILED_WORKER"
    assert diagnosis["portable_candidate_fields_equal"] is True
    assert diagnosis["node_local_only_differences"] is True


def test_portable_projection_rejects_missing_extra_and_rename_by_path() -> None:
    def missing(_root: Path, npz: Path) -> None:
        npz.unlink()

    def extra(root: Path, _npz: Path) -> None:
        leaf = root / "clips" / "clips" / "ff" / f"{'f' * 64}.npz"
        leaf.parent.mkdir(mode=0o700)
        leaf.write_bytes(b"extra")
        leaf.chmod(0o600)

    def rename(_root: Path, npz: Path) -> None:
        npz.rename(npz.with_name(f"{'d' * 64}.npz"))

    for mutation in (missing, extra, rename):
        with _one_candidate() as (root, relative, npz):
            mutation(root, npz)
            _expect_code(
                lambda: controller._r8u_r4_portable_metadata_projection(
                    root, expected_npz_paths=frozenset({relative})
                ),
                "R8U_PORTABLE_CANDIDATE_PATH_SET_MISMATCH",
            )


def test_portable_comparison_maps_control_metadata_and_semantic_drift() -> None:
    baseline = _portable_authority()
    changed_control = dict(baseline)
    changed_control["technical_disposition_manifest_sha256"] = OTHER_SHA
    _comparison_failure(
        baseline,
        changed_control,
        "R8U_PORTABLE_CANDIDATE_CONTROL_HASH_MISMATCH",
    )

    changed_size = dict(baseline)
    changed_size["candidate_npz_bytes"] += 1
    changed_size["candidate_relative_file_portable_projection_sha256"] = OTHER_SHA
    _comparison_failure(
        baseline,
        changed_size,
        "R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH",
    )

    same_size_substitution = dict(baseline)
    same_size_substitution["candidate_npz_manifest_projection_sha256"] = OTHER_SHA
    _comparison_failure(
        baseline,
        same_size_substitution,
        "R8U_PORTABLE_CANDIDATE_PATH_SET_MISMATCH",
    )

    changed_semantics = dict(baseline)
    changed_semantics["n_object_technical_dispositions"] = 1
    _comparison_failure(
        baseline,
        changed_semantics,
        "R8U_PORTABLE_CANDIDATE_SEMANTIC_MISMATCH",
    )


def test_portable_projection_rejects_mode_owner_type_link_and_zero_size() -> None:
    def wrong_mode(_root: Path, npz: Path):
        npz.chmod(0o640)
        return nullcontext()

    def zero_size(_root: Path, npz: Path):
        npz.write_bytes(b"")
        return nullcontext()

    def hardlink(root: Path, npz: Path):
        os.link(npz, root.parent / "extra-link.npz")
        return nullcontext()

    def fifo(_root: Path, npz: Path):
        npz.unlink()
        os.mkfifo(npz, 0o600)
        return nullcontext()

    @contextmanager
    def wrong_owner_context(root: Path, npz: Path) -> Iterator[None]:
        real_lstat = controller.os.lstat

        def changed(path: Any, *args: Any, **kwargs: Any) -> Any:
            observed = real_lstat(path, *args, **kwargs)
            if Path(path) != npz:
                return observed
            fields = (
                "st_mode", "st_uid", "st_gid", "st_dev", "st_ino",
                "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
            )
            values = {field: getattr(observed, field) for field in fields}
            values["st_uid"] += 1
            return SimpleNamespace(**values)

        with mock.patch.object(controller.os, "lstat", side_effect=changed):
            yield

    cases = (
        ("mode", wrong_mode),
        ("zero-size", zero_size),
        ("link", hardlink),
        ("type", fifo),
        ("owner", wrong_owner_context),
    )
    for _name, mutate in cases:
        with _one_candidate() as (root, relative, npz):
            context = mutate(root, npz)
            with context:
                _expect_code(
                    lambda: controller._r8u_r4_portable_metadata_projection(
                        root, expected_npz_paths=frozenset({relative})
                    ),
                    "R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH",
                )


def test_ctime_only_difference_has_closed_noncontent_classification() -> None:
    sealed = _with_locality(_portable_authority(), "same")
    observed = dict(sealed)
    field = "candidate_file_mtime_ctime_projection"
    sealed[field] = SHA
    observed[field] = OTHER_SHA
    result = controller._r8u_r4_compare_candidate_projection(sealed, observed)
    assert result["portable_candidate_fields_equal"] is True
    assert result["node_local_only_differences"] is True
    diagnosis = controller._r8u_r4_candidate_replay_diagnosis(
        comparison=result,
        candidate_path_set_equal=True,
        candidate_count_and_bytes_equal=True,
    )
    assert diagnosis["status"] == (
        "PASS_NONCONTENT_TIMESTAMP_REPLAY_DIFFERENCE"
    )


def test_diagnosis_is_aggregate_safe_exact_and_no_clobber() -> None:
    comparison = {
        "first_differing_field": "source_mount_identity_sha256",
        "differing_fields": ["source_mount_identity_sha256"],
        "historical_fields_not_recorded": [],
        "portable_candidate_fields_equal": True,
        "node_local_only_differences": True,
        "candidate_path_set_equal": True,
        "candidate_count_and_bytes_equal": True,
        "classification": "PASS_NODE_LOCAL_CANDIDATE_REPLAY_DIFFERENCE",
    }
    diagnosis = controller._r8u_r4_candidate_replay_diagnosis(
        job_id="7364184",
        candidate_seal_sha256=R3_SEAL_SHA256,
        comparison=comparison,
        candidate_path_set_equal=True,
        candidate_count_and_bytes_equal=True,
    )
    assert diagnosis["job_id"] == "7364184"
    assert diagnosis["candidate_seal_sha256"] == R3_SEAL_SHA256
    assert diagnosis["portable_candidate_fields_equal"] is True
    assert diagnosis["node_local_only_differences"] is True
    assert diagnosis["zero_body_reads"] is True
    assert diagnosis["npz_body_reads"] == 0
    assert diagnosis["dicom_body_reads"] == 0
    assert diagnosis["status"] == "PASS_NODE_LOCAL_CANDIDATE_REPLAY_DIFFERENCE"

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary).resolve() / "diagnosis.restricted.json"
        controller._r8u_r4_write_diagnosis_no_clobber(path, diagnosis)
        before = path.read_bytes()
        _expect_code(
            lambda: controller._r8u_r4_write_diagnosis_no_clobber(path, diagnosis),
            "R8U_R4_DIAGNOSIS_NO_CLOBBER_FAILED",
        )
        assert path.read_bytes() == before
        assert stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) == 0o600


def _locality_fixture(root: Path) -> tuple[Path, Path, Mapping[str, Any]]:
    source = root / "source-parent" / "dicom_extraction"
    target = root / "target-parent" / "dicom_extraction"
    source.mkdir(parents=True, mode=0o700)
    target.parent.mkdir(mode=0o700)
    source.chmod(0o700)
    source.parent.chmod(0o700)
    target.parent.chmod(0o700)
    return source, target, {
        "status": "AUTHORIZED_EXCLUSIVE_R8U_R4_BATCH16_PUBLICATION"
    }


def test_live_locality_rejects_target_cross_mount_parent_change_and_claim() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        source, target, claim = _locality_fixture(Path(temporary).resolve())
        source.rmdir()
        _expect_code(
            lambda: controller._r8u_r4_live_publication_locality(
                source=source, target=target, publication_claim=claim
            ),
            "R8U_LIVE_PUBLICATION_SOURCE_INVALID",
        )

    with tempfile.TemporaryDirectory() as temporary:
        source, target, claim = _locality_fixture(Path(temporary).resolve())
        target.mkdir(mode=0o700)
        _expect_code(
            lambda: controller._r8u_r4_live_publication_locality(
                source=source, target=target, publication_claim=claim
            ),
            "R8U_LIVE_PUBLICATION_TARGET_INVALID",
        )

    with tempfile.TemporaryDirectory() as temporary:
        source, target, claim = _locality_fixture(Path(temporary).resolve())
        _expect_code(
            lambda: controller._r8u_r4_live_publication_locality(
                source=source,
                target=target,
                publication_claim=claim,
                mount_reader=lambda path: "source" if path == source.parent else "target",
            ),
            "R8U_LIVE_PUBLICATION_CROSS_MOUNT",
        )

    with tempfile.TemporaryDirectory() as temporary:
        source, target, claim = _locality_fixture(Path(temporary).resolve())
        calls: dict[Path, int] = {}

        def changing_identity(path: Path) -> Mapping[str, Any]:
            calls[path] = calls.get(path, 0) + 1
            generation = calls[path] if path == source.parent else 1
            return {
                "identity": f"{path.name}:{generation}",
                "mode": 0o700,
                "uid": os.geteuid(),
                "type": "directory",
            }

        _expect_code(
            lambda: controller._r8u_r4_live_publication_locality(
                source=source,
                target=target,
                publication_claim=claim,
                identity_reader=changing_identity,
                mount_reader=lambda _path: "same",
            ),
            "R8U_LIVE_PUBLICATION_PARENT_CHANGED",
        )

    with tempfile.TemporaryDirectory() as temporary:
        source, target, _claim = _locality_fixture(Path(temporary).resolve())
        _expect_code(
            lambda: controller._r8u_r4_live_publication_locality(
                source=source, target=target, publication_claim={}
            ),
            "R8U_LIVE_PUBLICATION_CLAIM_INVALID",
        )


def test_primitive_is_worker_only_and_publication_precedes_echoprime() -> None:
    submit = inspect.getsource(controller.submit_r8u_r4_batch16_publication_resume)
    worker = inspect.getsource(controller.run_r8u_r4_batch16_publication_resume)
    assert "_r8u_r4_primitive_probe(" not in submit
    assert "_r8u_r4_publish_candidate(" not in submit
    assert "_r8u_r4_primitive_probe(" in worker
    assert "_r8u_r4_publish_candidate(" in worker
    assert "dependency.echoprime(" in worker
    assert worker.index("_r8u_r4_publish_candidate(") < worker.index(
        "dependency.echoprime("
    )
    assert "dependency.dicom(" not in worker
    assert "dependency.download(" not in worker
    assert "download_mimic_echo_subset" not in worker
    assert "run_production_dicom_extraction" not in worker
    assert "predict(" not in worker.casefold()
    assert "fit(" not in worker.casefold()


def test_submit_has_one_capacity_qsub_qstat_and_no_polling() -> None:
    source = inspect.getsource(controller.submit_r8u_r4_batch16_publication_resume)
    assert "runtime_validation_context=stages.SEALED_SCHEDULER_RUNTIME_REPLAY" in source
    assert "runtime_validation_context=stages.LIVE_RUNTIME_CAPTURE" not in source
    assert source.count("probe_fixed_r8u_r4_batch16_publication_resume_capacity(") == 1
    assert source.count("scheduler._capture_qsub(") == 1
    assert source.count("_validate_r8u_r4_initial_qstat(") == 1
    assert "time.sleep(" not in source
    assert "while " not in source
    assert "submit_r8u_r4_continuation_17_19(" not in source


def test_r3_artifacts_are_read_only_and_r4_diagnosis_cannot_touch_them() -> None:
    assert controller.R8U_R4_ROOT != controller.R8U_R3_ROOT
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        r3 = root / "r3-seal.json"
        r3.write_bytes(b"immutable-r3\n")
        r3.chmod(0o600)
        before = (
            r3.read_bytes(),
            r3.stat(follow_symlinks=False).st_mode,
            hashlib.sha256(r3.read_bytes()).hexdigest(),
        )
        diagnosis = controller._r8u_r4_candidate_replay_diagnosis(
            comparison={
                "first_differing_field": "NONE",
                "differing_fields": [],
                "portable_differing_fields": [],
                "node_local_differing_fields": [],
                "portable_candidate_fields_equal": True,
                "node_local_only_differences": False,
                "candidate_path_set_equal": True,
                "candidate_count_and_bytes_equal": True,
            }
        )
        r4_root = root / "r4"
        r4_root.mkdir(mode=0o700)
        controller._r8u_r4_write_diagnosis_no_clobber(
            r4_root / "diagnosis.json", diagnosis
        )
        after = (
            r3.read_bytes(),
            r3.stat(follow_symlinks=False).st_mode,
            hashlib.sha256(r3.read_bytes()).hexdigest(),
        )
        assert after == before


def test_r4_implementation_is_exactly_one_child_of_r3_repair() -> None:
    starting = "a6e80b606a76dde2512a8eedf4eb8fa4f87211ef"
    assert (
        controller.R8U_R3_CANDIDATE_AUTHORITY_REPAIR_IMPLEMENTATION_COMMIT
        == starting
    )

    def git(*arguments: str) -> str:
        if arguments[:3] == ("rev-list", "--parents", "-n"):
            assert arguments[4] == COMMIT
            return f"{COMMIT} {starting}"
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            return ""
        if arguments[:2] == ("rev-list", "--count"):
            return "1"
        raise AssertionError(arguments)

    with (
        mock.patch.object(controller.sequential, "_current_commit", return_value=COMMIT),
        mock.patch.object(controller.sequential, "_git", side_effect=git),
    ):
        assert controller._current_r8u_r4_implementation_commit() == COMMIT

    with (
        mock.patch.object(controller.sequential, "_current_commit", return_value=COMMIT),
        mock.patch.object(
            controller.sequential,
            "_git",
            side_effect=lambda *args: "2"
            if args[:2] == ("rev-list", "--count")
            else git(*args),
        ),
    ):
        _expect_code(
            controller._current_r8u_r4_implementation_commit,
            "R8U_R4_IMPLEMENTATION_ANCESTRY_INVALID",
        )


def test_future_continuation_and_finalizer_expose_closed_r4_terminal_links() -> None:
    required_links = {
        "r8u_r3_candidate_seal_sha256",
        "portable_candidate_authority_sha256",
        "candidate_replay_diagnosis_sha256",
        "publication_primitive_probe_sha256",
        "publication_claim_sha256",
        "publication_receipt_sha256",
        "resume_accounting_sha256",
        "resume_terminal_receipt_sha256",
    }
    assert required_links <= controller.R8U_R4_CONTINUATION_LINK_KEYS
    assert required_links <= finalizer.R8U_R4_CONTINUATION_LINK_KEYS
    for controller_name, finalizer_name in (
        ("R8U_R4_COMMON_KEYS", "R8U_R4_COMMON_CHAIN_KEYS"),
        ("R8U_R4_DIAGNOSIS_KEYS", "R8U_R4_DIAGNOSIS_KEYS"),
        ("R8U_R4_PORTABLE_AUTHORITY_KEYS", "R8U_R4_PORTABLE_AUTHORITY_KEYS"),
        ("R8U_R4_LOCALITY_KEYS", "R8U_R4_LOCALITY_KEYS"),
        ("R8U_R4_PUBLICATION_CLAIM_KEYS", "R8U_R4_PUBLICATION_CLAIM_KEYS"),
        ("R8U_R4_PROBE_KEYS", "R8U_R4_PROBE_KEYS"),
        ("R8U_R4_PUBLICATION_KEYS", "R8U_R4_PUBLICATION_KEYS"),
        ("R8U_R4_AUTHORITY_KEYS", "R8U_R4_AUTHORITY_KEYS"),
        ("R8U_R4_SUBMISSION_KEYS", "R8U_R4_SUBMISSION_KEYS"),
        ("R8U_R4_ACCOUNTING_KEYS", "R8U_R4_ACCOUNTING_KEYS"),
        ("R8U_R4_TERMINAL_KEYS", "R8U_R4_TERMINAL_KEYS"),
    ):
        assert getattr(controller, controller_name) == getattr(
            finalizer, finalizer_name
        )
    assert callable(controller.validate_r8u_r4_continuation_worker_submission)
    sequential_source = inspect.getsource(controller.sequential.run_batch_task)
    assert "R8U_R4_FIXED_CONTINUATION" in sequential_source
    assert "validate_r8u_r4_frozen_partial_evidence(" in sequential_source
    assert "validate_r8u_r4_continuation_worker_submission(" in sequential_source
    assert hasattr(finalizer, "R8UR4ImplementationAuthority")
    assert callable(finalizer._validate_r8u_r4_mixed_implementation_epochs)
    assert "r8u_r4_implementation_authority" in inspect.signature(
        finalizer.finalize_receipts
    ).parameters


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
