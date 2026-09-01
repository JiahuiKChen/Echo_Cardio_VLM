#!/usr/bin/env python3
"""Focused dependency-light R8U-R6 locality/publication proofs."""
from __future__ import annotations

import ast
from contextlib import contextmanager
import errno
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


R5_COMMIT = "7b7c3657e110b53a6e6112567410243377437db4"
R6_COMMIT = "c" * 40
SHA = "a" * 64


def _code(exc: BaseException) -> str:
    return str(getattr(exc, "code", exc))


def _expect_code(action: Callable[[], Any], expected: str) -> None:
    try:
        action()
    except Exception as exc:
        assert _code(exc) == expected, (_code(exc), expected)
    else:
        raise AssertionError(f"expected {expected}")


def _source(function: Callable[..., Any]) -> str:
    return inspect.getsource(function)


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _call_names(function: Callable[..., Any]) -> tuple[str, ...]:
    tree = ast.parse(_source(function))
    return tuple(
        _dotted_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )


def _has_while(function: Callable[..., Any]) -> bool:
    tree = ast.parse(_source(function))
    return any(isinstance(node, ast.While) for node in ast.walk(tree))


def _only_position(source: str, names: tuple[str, ...]) -> int:
    positions = [source.find(name) for name in names if source.find(name) >= 0]
    assert positions, names
    return min(positions)


def _identity(**changes: Any) -> dict[str, Any]:
    value = {
        "device": 10,
        "inode": 20,
        "type": "directory",
        "mode": 0o700,
        "uid": os.geteuid(),
        "gid": os.getegid(),
        "nlink": 3,
        "size": 4096,
        "mtime_ns": 100,
        "ctime_ns": 200,
    }
    value.update(changes)
    return value


def _candidate_fixture(root: Path) -> tuple[PurePosixPath, Path]:
    root.mkdir(mode=0o700)
    key = "b" * 64
    relative = PurePosixPath("clips") / "clips" / key[:2] / f"{key}.npz"
    path = root / relative
    path.parent.mkdir(parents=True, mode=0o700)
    current = path.parent
    while current != root.parent:
        current.chmod(0o700)
        if current == root:
            break
        current = current.parent
    path.write_bytes(b"opaque-test-body")
    path.chmod(0o600)
    return relative, path


@contextmanager
def _forbid_scientific_body_opens() -> Iterator[None]:
    real_os_open = os.open
    real_path_open = Path.open

    def guarded_os_open(path: Any, *args: Any, **kwargs: Any) -> int:
        if isinstance(path, (str, os.PathLike)) and str(path).lower().endswith(
            (".npz", ".dcm")
        ):
            raise AssertionError("R8U-R6 opened a scientific body")
        return real_os_open(path, *args, **kwargs)

    def guarded_path_open(path: Path, *args: Any, **kwargs: Any):
        if path.suffix.lower() in {".npz", ".dcm"}:
            raise AssertionError("R8U-R6 opened a scientific body")
        return real_path_open(path, *args, **kwargs)

    with (
        mock.patch.object(controller.os, "open", side_effect=guarded_os_open),
        mock.patch.object(Path, "open", guarded_path_open),
    ):
        yield


def test_r6_api_is_additive_and_epoch_is_fresh() -> None:
    required = (
        "_current_r8u_r6_implementation_commit",
        "_r8u_r6_final_publication_locality",
        "_r8u_r6_publish_candidate",
        "submit_r8u_r6_locality_sequence_probe",
        "run_r8u_r6_locality_sequence_probe",
        "adjudicate_r8u_r6_locality_sequence_probe",
        "submit_r8u_r6_batch16_publication_resume",
        "run_r8u_r6_batch16_publication_resume",
        "validate_r8u_r6_resume_terminal",
        "submit_r8u_r6_continuation_17_19",
        "validate_r8u_r6_continuation_worker_submission",
        "run_r8u_r6_continuation_array_task",
        "run_r8u_r6_continuation_finalizer",
    )
    for name in required:
        assert callable(getattr(controller, name)), name
    assert controller.R8U_R5_WORKER_CONTEXT_REPAIR_IMPLEMENTATION_COMMIT == R5_COMMIT
    assert controller.R8U_R5_FAILED_PUBLICATION_RESUME_JOB_ID == "7388079"
    assert controller.R8U_R5_FAILED_PUBLICATION_RESUME_LOG_SHA256 == (
        "55cf0f86199f3b1152909a2a6fe814aa9f598b9e7041879a37e33e55557a080e"
    )
    assert controller.R8U_R6_ROOT.name == "r8u_r6_batch16_publication_resume"
    assert controller.R8U_R6_ROOT != controller.R8U_R5_ROOT
    assert controller.R8U_R6_PUBLICATION_CLAIM_ROOT != (
        controller.R8U_R5_PUBLICATION_CLAIM_ROOT
    )


def test_r6_implementation_is_one_direct_child_of_consumed_r5() -> None:
    def git(*arguments: str) -> str:
        if arguments[:3] == ("rev-list", "--parents", "-n"):
            assert arguments[4] == R6_COMMIT
            return f"{R6_COMMIT} {R5_COMMIT}"
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            assert arguments[2:] == (R5_COMMIT, R6_COMMIT)
            return ""
        if arguments[:2] == ("rev-list", "--count"):
            assert arguments[2] == f"{R5_COMMIT}..{R6_COMMIT}"
            return "1"
        raise AssertionError(arguments)

    with (
        mock.patch.object(
            controller.sequential, "_current_commit", return_value=R6_COMMIT
        ),
        mock.patch.object(controller.sequential, "_git", side_effect=git),
    ):
        assert controller._current_r8u_r6_implementation_commit() == R6_COMMIT

    with (
        mock.patch.object(
            controller.sequential, "_current_commit", return_value=R6_COMMIT
        ),
        mock.patch.object(
            controller.sequential,
            "_git",
            side_effect=lambda *args: "2"
            if args[:2] == ("rev-list", "--count")
            else git(*args),
        ),
    ):
        _expect_code(
            controller._current_r8u_r6_implementation_commit,
            "R8U_R6_IMPLEMENTATION_ANCESTRY_INVALID",
        )


def test_r6_scheduler_account_authority_binds_all_common_fields() -> None:
    sealed = {"HOME": "/tmp", "LOGNAME": "worker", "USER": "worker"}
    value = {
        **controller._r8u_r6_common(
            artifact_type="lvef_c3_r8u_r6_scheduler_account_authority_v1",
            status="AUTHORIZED_R8U_R6_SCHEDULER_ACCOUNT",
            implementation_commit=R6_COMMIT,
        ),
        "expected_effective_uid": 1234,
        "expected_scheduler_username": "worker",
        "canonical_home": "/tmp",
        "submitter_passwd_lookup_available": True,
        "runner_sha256": SHA,
        "python_sha256": SHA,
        "qsub_environment_sha256": (
            controller.scheduler.qsub_environment_sha256(sealed)
        ),
        "sealed_qsub_environment": sealed,
        "authorized_worker_roles": list(controller.R8U_R6_WORKER_ROLES),
    }
    assert controller.validate_r8u_r6_scheduler_account_authority(value) is value
    for field in (
        "schema_version", "original_scientific_commit", "attempt_id",
        "batch_plan_sha256", "batch_id",
    ):
        tampered = dict(value)
        tampered[field] = 2 if field == "schema_version" else "tampered"
        _expect_code(
            lambda tampered=tampered:
                controller.validate_r8u_r6_scheduler_account_authority(tampered),
            "R8U_R6_SCHEDULER_ACCOUNT_AUTHORITY_INVALID",
        )


def test_stable_and_volatile_identity_field_sets_are_exact() -> None:
    assert tuple(controller.R8U_R6_STABLE_IDENTITY_FIELDS) == (
        "device", "inode", "type", "mode", "uid", "gid",
    )
    assert tuple(controller.R8U_R6_VOLATILE_IDENTITY_FIELDS) == (
        "nlink", "size", "mtime_ns", "ctime_ns",
    )
    assert set(controller.R8U_R6_STABLE_IDENTITY_FIELDS).isdisjoint(
        controller.R8U_R6_VOLATILE_IDENTITY_FIELDS
    )
    assert controller._r8u_r6_stable_identity(_identity()) == {
        field: _identity()[field]
        for field in controller.R8U_R6_STABLE_IDENTITY_FIELDS
    }
    assert controller._r8u_r6_volatile_identity(_identity()) == {
        field: _identity()[field]
        for field in controller.R8U_R6_VOLATILE_IDENTITY_FIELDS
    }
    for malformed in (
        {**_identity(), "unexpected": 1},
        {key: value for key, value in _identity().items() if key != "inode"},
        {**_identity(), "device": True},
    ):
        _expect_code(
            lambda value=malformed: controller._r8u_r6_stable_identity(value),
            "R8U_PUBLICATION_LOCALITY_INVALID",
        )


def test_exact_and_volatile_only_directory_changes_are_nonblocking() -> None:
    baseline = _identity()
    assert controller._r8u_r6_compare_identity(
        before=baseline, after=dict(baseline), role="source_parent"
    ) == []
    for field in controller.R8U_R6_VOLATILE_IDENTITY_FIELDS:
        observed = {**baseline, field: baseline[field] + 1}
        assert controller._r8u_r6_compare_identity(
            before=baseline, after=observed, role="source_parent"
        ) == [f"source_parent.{field}"]
    observed = {
        **baseline,
        "nlink": baseline["nlink"] + 1,
        "size": baseline["size"] + 8,
        "mtime_ns": baseline["mtime_ns"] + 2,
        "ctime_ns": baseline["ctime_ns"] + 3,
    }
    assert controller._r8u_r6_compare_identity(
        before=baseline, after=observed, role="target_parent"
    ) == [
        "target_parent.nlink",
        "target_parent.size",
        "target_parent.mtime_ns",
        "target_parent.ctime_ns",
    ]


def test_stable_replacement_and_owner_mode_failures_are_field_specific() -> None:
    baseline = _identity()
    for role, field, expected in (
        ("source", "device", "R8U_PUBLICATION_SOURCE_OBJECT_CHANGED"),
        ("source", "inode", "R8U_PUBLICATION_SOURCE_OBJECT_CHANGED"),
        ("source", "type", "R8U_PUBLICATION_SOURCE_OBJECT_CHANGED"),
        (
            "source_parent", "device",
            "R8U_PUBLICATION_SOURCE_PARENT_OBJECT_CHANGED",
        ),
        (
            "source_parent", "inode",
            "R8U_PUBLICATION_SOURCE_PARENT_OBJECT_CHANGED",
        ),
        (
            "source_parent", "type",
            "R8U_PUBLICATION_SOURCE_PARENT_OBJECT_CHANGED",
        ),
        (
            "target_parent", "device",
            "R8U_PUBLICATION_TARGET_PARENT_OBJECT_CHANGED",
        ),
        (
            "target_parent", "inode",
            "R8U_PUBLICATION_TARGET_PARENT_OBJECT_CHANGED",
        ),
        (
            "target_parent", "type",
            "R8U_PUBLICATION_TARGET_PARENT_OBJECT_CHANGED",
        ),
        (
            "source_parent", "uid",
            "R8U_PUBLICATION_PARENT_OWNER_OR_MODE_CHANGED",
        ),
        (
            "source_parent", "gid",
            "R8U_PUBLICATION_PARENT_OWNER_OR_MODE_CHANGED",
        ),
        (
            "target_parent", "mode",
            "R8U_PUBLICATION_PARENT_OWNER_OR_MODE_CHANGED",
        ),
    ):
        changed: Any
        if field == "type":
            changed = "regular"
        elif field == "mode":
            changed = 0o750
        else:
            changed = baseline[field] + 1
        _expect_code(
            lambda role=role, field=field, changed=changed: (
                controller._r8u_r6_compare_identity(
                    before=baseline,
                    after={**baseline, field: changed},
                    role=role,
                )
            ),
            expected,
        )


def test_directory_validation_keeps_type_owner_and_mode_policy_closed() -> None:
    controller._r8u_r6_validate_directory_identity(_identity(), source=True)
    controller._r8u_r6_validate_directory_identity(_identity(), source=False)
    _expect_code(
        lambda: controller._r8u_r6_validate_directory_identity(
            _identity(type="regular"), source=True
        ),
        "R8U_PUBLICATION_SOURCE_OBJECT_CHANGED",
    )
    for changes in (
        {"uid": os.geteuid() + 1},
        {"mode": 0o750},
    ):
        _expect_code(
            lambda changes=changes: controller._r8u_r6_validate_directory_identity(
                _identity(**changes), source=False
            ),
            "R8U_PUBLICATION_PARENT_OWNER_OR_MODE_CHANGED",
        )


def test_empty_primitive_probe_is_clean_and_einval_is_the_supported_fallback() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        work_root = Path(temporary).resolve() / "probe"
        with mock.patch.object(
            controller, "_r8u_r3_mount_authority", return_value=((8,), SHA)
        ):
            value = controller._r8u_r6_run_empty_primitive_probe(
                work_root=work_root,
                expected_mount_key=(8,),
                invoker=lambda _source, _target: errno.EINVAL,
            )
        assert value == {
            "primary_primitive": "RENAMEAT2_RENAME_NOREPLACE",
            "primary_result": "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
            "primary_errno": "EINVAL",
            "primary_errno_number": errno.EINVAL,
            "primary_returned_success": False,
            "probe_source_present_after": True,
            "probe_target_present_after": False,
            "probe_target_exact_after": False,
            "probe_cleanup_passed": True,
            "probe_directories_created": 2,
            "probe_directories_removed": 2,
        }
        assert not work_root.exists()


def test_empty_primitive_probe_blocks_mount_permission_io_and_collision() -> None:
    cases = (
        (errno.EXDEV, "R8U_PUBLICATION_PARENT_MOUNT_CHANGED"),
        (errno.ENOSYS, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.EOPNOTSUPP, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.EACCES, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.EPERM, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.EIO, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.EEXIST, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.ENOTEMPTY, "R8U_PUBLICATION_LOCALITY_INVALID"),
    )
    for number, expected in cases:
        with tempfile.TemporaryDirectory() as temporary:
            work_root = Path(temporary).resolve() / "probe"
            with mock.patch.object(
                controller, "_r8u_r3_mount_authority", return_value=((8,), SHA)
            ):
                _expect_code(
                    lambda number=number: controller._r8u_r6_run_empty_primitive_probe(
                        work_root=work_root,
                        expected_mount_key=(8,),
                        invoker=lambda _source, _target: number,
                    ),
                    expected,
                )
            assert not work_root.exists()

    with tempfile.TemporaryDirectory() as temporary:
        work_root = Path(temporary).resolve() / "probe"
        calls = 0

        def forbidden(_source: Path, _target: Path) -> int:
            nonlocal calls
            calls += 1
            return errno.EINVAL

        with mock.patch.object(
            controller, "_r8u_r3_mount_authority", return_value=((9,), SHA)
        ):
            _expect_code(
                lambda: controller._r8u_r6_run_empty_primitive_probe(
                    work_root=work_root,
                    expected_mount_key=(8,),
                    invoker=forbidden,
                ),
                "R8U_PUBLICATION_PARENT_MOUNT_CHANGED",
            )
        assert calls == 0


def _run_locality_sequence(
    *,
    root: Path,
    identity_reader: Callable[[Path], Mapping[str, Any]] | None = None,
    mount_reader: Callable[[Path], Any] | None = None,
    probe_invoker: Callable[[Path, Path], Any] | None = None,
    competing_jobs: int = 0,
    competing_processes: int = 0,
) -> Mapping[str, Any]:
    source = root / "source-parent" / "dicom_extraction"
    target = root / "target-parent" / "dicom_extraction"
    source.mkdir(parents=True, mode=0o700)
    target.parent.mkdir(mode=0o700)
    source.chmod(0o700)
    source.parent.chmod(0o700)
    target.parent.chmod(0o700)
    with (
        mock.patch.object(
            controller, "R8U_R6_CPU_PROBE_WORK_ROOT", root / "probe-work"
        ),
        mock.patch.object(
            controller, "_r8u_r3_mount_authority", return_value=((8,), SHA)
        ),
        mock.patch.object(controller.core, "sha256_file", return_value=SHA),
    ):
        return controller._r8u_r6_locality_sequence_diagnostic(
            source=source,
            target=target,
            implementation_commit=R6_COMMIT,
            probe_job_id="8123456",
            worker_diagnostic={"safe": True},
            worker_qstat_projection={
                "competing_matching_jobs": competing_jobs,
            },
            worker_process_projection={
                "matching_processes": competing_processes,
            },
            identity_reader=identity_reader,
            mount_reader=mount_reader or (lambda _path: (8,)),
            probe_invoker=probe_invoker or (
                lambda _source, _target: errno.EINVAL
            ),
        )


def test_cpu_locality_sequence_rechecks_nonsymlink_components_after_probe() -> None:
    original = controller.sequential._require_nonsymlink_components
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        checked: list[Path] = []

        def record(path: Path) -> None:
            checked.append(Path(path))
            original(path)

        with mock.patch.object(
            controller.sequential,
            "_require_nonsymlink_components",
            side_effect=record,
        ):
            _run_locality_sequence(root=root)
        source = root / "source-parent" / "dicom_extraction"
        target_parent = root / "target-parent"
        locality_checks = [
            path for path in checked if path in {source, target_parent}
        ]
        assert locality_checks == [source, target_parent, source, target_parent]

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source = root / "source-parent" / "dicom_extraction"
        source_checks = 0

        def replace_after_probe(path: Path) -> None:
            nonlocal source_checks
            if Path(path) == source:
                source_checks += 1
            if source_checks == 2 and Path(path) == source:
                raise RuntimeError("simulated ancestor replacement")
            original(path)

        with mock.patch.object(
            controller.sequential,
            "_require_nonsymlink_components",
            side_effect=replace_after_probe,
        ):
            _expect_code(
                lambda: _run_locality_sequence(root=root),
                "R8U_PUBLICATION_LOCALITY_INVALID",
            )
        assert source_checks == 2


def test_cpu_locality_sequence_accepts_exact_and_volatile_only_changes() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        exact = _run_locality_sequence(root=root)
        assert exact["status"] == "PASS_R8U_R6_STABLE_PUBLICATION_LOCALITY"
        assert exact["volatile_changed_fields"] == []
        assert exact["volatile_metadata_classification"] == "NONE"
        assert all(exact["source_stable_fields_equal"].values())
        assert all(exact["source_parent_stable_fields_equal"].values())
        assert all(exact["target_parent_stable_fields_equal"].values())

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source = root / "source-parent" / "dicom_extraction"
        source_parent = source.parent
        target_parent = root / "target-parent"
        calls: dict[Path, int] = {}

        def changing(path: Path) -> Mapping[str, Any]:
            path = Path(path)
            calls[path] = calls.get(path, 0) + 1
            value = _identity(inode={
                source: 21, source_parent: 22, target_parent: 23,
            }[path])
            if calls[path] > 1:
                if path == source:
                    value["nlink"] += 1
                elif path == source_parent:
                    value["mtime_ns"] += 1
                    value["ctime_ns"] += 1
                else:
                    value["size"] += 1
            return value

        changed = _run_locality_sequence(root=root, identity_reader=changing)
        assert changed["status"] == "PASS_R8U_R6_STABLE_PUBLICATION_LOCALITY"
        assert changed["volatile_metadata_classification"] == (
            "R8U_PUBLICATION_VOLATILE_METADATA_ONLY_CHANGED"
        )
        assert changed["volatile_changed_fields"] == [
            "source.nlink",
            "source_parent.ctime_ns",
            "source_parent.mtime_ns",
            "target_parent.size",
        ]
        assert changed["source_volatile_fields_equal"]["nlink"] is False
        assert changed["source_parent_volatile_fields_equal"]["mtime_ns"] is False
        assert changed["source_parent_volatile_fields_equal"]["ctime_ns"] is False
        assert changed["target_parent_volatile_fields_equal"]["size"] is False
        forbidden_raw = {
            "device", "inode", "uid", "gid", "size", "mtime_ns", "ctime_ns",
        }
        assert set(changed).isdisjoint(forbidden_raw)


def test_cpu_locality_sequence_blocks_stable_mount_target_and_competitors() -> None:
    for role, expected in (
        ("source", "R8U_PUBLICATION_SOURCE_OBJECT_CHANGED"),
        ("source_parent", "R8U_PUBLICATION_SOURCE_PARENT_OBJECT_CHANGED"),
        ("target_parent", "R8U_PUBLICATION_TARGET_PARENT_OBJECT_CHANGED"),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "source-parent" / "dicom_extraction"
            paths = {
                source: "source",
                source.parent: "source_parent",
                root / "target-parent": "target_parent",
            }
            calls: dict[Path, int] = {}

            def replaced(path: Path) -> Mapping[str, Any]:
                path = Path(path)
                calls[path] = calls.get(path, 0) + 1
                value = _identity(inode={
                    "source": 21, "source_parent": 22, "target_parent": 23,
                }[paths[path]])
                if paths[path] == role and calls[path] > 1:
                    value["inode"] += 100
                return value

            _expect_code(
                lambda: _run_locality_sequence(root=root, identity_reader=replaced),
                expected,
            )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        mounts = iter(((8,), (8,), (9,), (8,)))
        _expect_code(
            lambda: _run_locality_sequence(
                root=root, mount_reader=lambda _path: next(mounts)
            ),
            "R8U_PUBLICATION_PARENT_MOUNT_CHANGED",
        )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        target = root / "target-parent" / "dicom_extraction"

        def target_appears(_left: Path, _right: Path) -> int:
            target.mkdir(mode=0o700)
            return errno.EINVAL

        _expect_code(
            lambda: _run_locality_sequence(
                root=root, probe_invoker=target_appears
            ),
            "R8U_PUBLICATION_TARGET_APPEARED",
        )

    for jobs, processes in ((1, 0), (0, 1)):
        with tempfile.TemporaryDirectory() as temporary:
            _expect_code(
                lambda jobs=jobs, processes=processes: _run_locality_sequence(
                    root=Path(temporary).resolve(),
                    competing_jobs=jobs,
                    competing_processes=processes,
                ),
                "R8U_PUBLICATION_LOCALITY_INVALID",
            )


def test_candidate_directory_volatile_drift_passes_without_body_reads() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "dicom_extraction"
        relative, _npz = _candidate_fixture(root)
        real_identity = controller._r8u_r4_local_identity
        calls: dict[Path, int] = {}

        def volatile_identity(path: Path) -> Mapping[str, Any]:
            value = dict(real_identity(Path(path)))
            if value["type"] == "directory":
                key = Path(path)
                calls[key] = calls.get(key, 0) + 1
                value["mtime_ns"] += calls[key]
                value["ctime_ns"] += calls[key]
                value["size"] += calls[key]
                value["nlink"] += calls[key]
            return value

        with (
            _forbid_scientific_body_opens(),
            mock.patch.object(
                controller, "_r8u_r4_local_identity", side_effect=volatile_identity
            ),
        ):
            projection = controller._r8u_r4_portable_metadata_projection(
                root,
                expected_npz_paths=frozenset({relative}),
                stable_directory_identity=True,
            )
        assert projection.value["candidate_npz_files"] == 1
        assert projection.value["candidate_npz_bytes"] == len(b"opaque-test-body")


def test_candidate_regular_file_size_mode_and_nlink_remain_strict() -> None:
    mutators = (
        lambda _root, path: path.write_bytes(b""),
        lambda _root, path: path.chmod(0o640),
        lambda root, path: os.link(path, root.parent / "second-link.npz"),
    )
    for mutate in mutators:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "dicom_extraction"
            relative, npz = _candidate_fixture(root)
            mutate(root, npz)
            _expect_code(
                lambda: controller._r8u_r4_portable_metadata_projection(
                    root,
                    expected_npz_paths=frozenset({relative}),
                    stable_directory_identity=True,
                ),
                "R8U_PORTABLE_CANDIDATE_FILE_METADATA_MISMATCH",
            )


def test_final_locality_schema_is_aggregate_safe_and_probe_bound() -> None:
    required = {
        "publication_claim_sha256",
        "publication_primitive_probe_sha256",
        "stable_identity_fields",
        "volatile_identity_fields",
        "source_stable_identity_equal",
        "source_parent_stable_identity_equal",
        "target_parent_stable_identity_equal",
        "source_volatile_fields_equal",
        "source_parent_volatile_fields_equal",
        "target_parent_volatile_fields_equal",
        "volatile_changed_fields",
        "volatile_metadata_classification",
        "captured_after_primitive_probe",
        "probe_cleanup_validated",
    }
    assert required <= controller.R8U_R6_FINAL_LOCALITY_KEYS
    forbidden = {
        "source_identity",
        "source_parent_identity",
        "target_parent_identity",
        "source_device",
        "source_inode",
        "uid",
        "gid",
        "size",
        "mtime_ns",
        "ctime_ns",
    }
    assert controller.R8U_R6_FINAL_LOCALITY_KEYS.isdisjoint(forbidden)
    source = _source(controller._r8u_r6_final_publication_locality)
    assert "publication_primitive_probe_sha256" in source
    assert "captured_after_primitive_probe" in source
    assert "probe_cleanup_validated" in source
    assert "_r8u_r4_revalidate_live_locality(" not in source


def test_final_locality_rejects_mount_target_claim_and_competitor_drift() -> None:
    source = _source(controller._r8u_r6_final_publication_locality)
    for code in (
        "R8U_PUBLICATION_PARENT_MOUNT_CHANGED",
        "R8U_PUBLICATION_TARGET_APPEARED",
        "R8U_PUBLICATION_LOCALITY_INVALID",
    ):
        assert code in source
    assert "publication_claim" in source
    assert "probe" in source
    assert "competing_active_jobs" in source
    assert "competing_active_processes" in source
    assert "os.path.lexists(target)" in source


def _run_final_locality(
    *,
    root: Path,
    identity_reader: Callable[[Path], Mapping[str, Any]] | None = None,
    mount_reader: Callable[[Path], Any] | None = None,
    competing_jobs: int = 0,
    competing_processes: int = 0,
) -> controller._R8UR6PublicationLocality:
    source = root / "source-parent" / "dicom_extraction"
    target = root / "target-parent" / "dicom_extraction"
    source.mkdir(parents=True, mode=0o700)
    target.parent.mkdir(mode=0o700)
    source.chmod(0o700)
    source.parent.chmod(0o700)
    target.parent.chmod(0o700)
    claim = {"status": "AUTHORIZED_EXCLUSIVE_R8U_R6_BATCH16_PUBLICATION"}
    probe = {
        "status": "PASS_R8U_R6_PUBLICATION_PRIMITIVE_PROBE",
        "publication_claim_sha256": SHA,
        "probe_cleanup_passed": True,
    }
    with (
        mock.patch.object(
            controller, "_validate_r8u_r6_publication_claim", return_value=claim
        ),
        mock.patch.object(
            controller, "_validate_r8u_r6_primitive_probe", return_value=probe
        ),
        mock.patch.object(controller.core, "sha256_file", return_value=SHA),
        mock.patch.object(
            controller.core, "canonical_json_sha256", return_value=SHA
        ),
    ):
        return controller._r8u_r6_final_publication_locality(
            source=source,
            target=target,
            implementation_commit=R6_COMMIT,
            publication_claim=claim,
            probe=probe,
            worker_context_diagnostic_sha256=SHA,
            worker_qstat_projection={
                "competing_matching_jobs": competing_jobs,
            },
            worker_process_projection={
                "matching_processes": competing_processes,
            },
            identity_reader=identity_reader,
            mount_reader=mount_reader or (lambda _path: (8,)),
        )


def test_final_locality_accepts_only_volatile_drift_after_probe() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        exact = _run_final_locality(root=Path(temporary).resolve())
        assert exact["status"] == "PASS_R8U_R6_FINAL_PUBLICATION_LOCALITY"
        assert exact["captured_after_primitive_probe"] is True
        assert exact["probe_cleanup_validated"] is True
        assert exact["volatile_changed_fields"] == []

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source = root / "source-parent" / "dicom_extraction"
        source_parent = source.parent
        target_parent = root / "target-parent"
        calls: dict[Path, int] = {}

        def changing(path: Path) -> Mapping[str, Any]:
            path = Path(path)
            calls[path] = calls.get(path, 0) + 1
            value = _identity(inode={
                source: 21, source_parent: 22, target_parent: 23,
            }[path])
            if calls[path] > 1:
                value[{
                    source: "nlink",
                    source_parent: "mtime_ns",
                    target_parent: "ctime_ns",
                }[path]] += 1
            return value

        locality = _run_final_locality(root=root, identity_reader=changing)
        assert locality["volatile_metadata_classification"] == (
            "R8U_PUBLICATION_VOLATILE_METADATA_ONLY_CHANGED"
        )
        assert locality["volatile_changed_fields"] == [
            "source.nlink", "source_parent.mtime_ns", "target_parent.ctime_ns",
        ]
        assert all(
            locality[field] is True
            for field in (
                "source_stable_identity_equal",
                "source_parent_stable_identity_equal",
                "target_parent_stable_identity_equal",
            )
        )


def test_final_locality_functionally_blocks_claim_mount_target_and_competitors() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source = root / "source-parent" / "dicom_extraction"
        target = root / "target-parent" / "dicom_extraction"
        source.mkdir(parents=True, mode=0o700)
        target.parent.mkdir(mode=0o700)
        _expect_code(
            lambda: controller._r8u_r6_final_publication_locality(
                source=source,
                target=target,
                implementation_commit=R6_COMMIT,
                publication_claim={},
                probe={},
                worker_context_diagnostic_sha256=SHA,
                worker_qstat_projection={"competing_matching_jobs": 0},
                worker_process_projection={"matching_processes": 0},
            ),
            "R8U_R6_PUBLICATION_CLAIM_INVALID",
        )

    with tempfile.TemporaryDirectory() as temporary:
        mounts = iter(((8,), (8,), (9,), (8,)))
        _expect_code(
            lambda: _run_final_locality(
                root=Path(temporary).resolve(),
                mount_reader=lambda _path: next(mounts),
            ),
            "R8U_PUBLICATION_PARENT_MOUNT_CHANGED",
        )

    for jobs, processes in ((1, 0), (0, 1)):
        with tempfile.TemporaryDirectory() as temporary:
            _expect_code(
                lambda jobs=jobs, processes=processes: _run_final_locality(
                    root=Path(temporary).resolve(),
                    competing_jobs=jobs,
                    competing_processes=processes,
                ),
                "R8U_PUBLICATION_LOCALITY_INVALID",
            )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        source = root / "source-parent" / "dicom_extraction"
        target = root / "target-parent" / "dicom_extraction"
        source.mkdir(parents=True, mode=0o700)
        target.mkdir(parents=True, mode=0o700)
        claim = {"status": "AUTHORIZED_EXCLUSIVE_R8U_R6_BATCH16_PUBLICATION"}
        probe = {"publication_claim_sha256": SHA, "probe_cleanup_passed": True}
        with (
            mock.patch.object(
                controller, "_validate_r8u_r6_publication_claim", return_value=claim
            ),
            mock.patch.object(
                controller, "_validate_r8u_r6_primitive_probe", return_value=probe
            ),
            mock.patch.object(controller.core, "sha256_file", return_value=SHA),
            mock.patch.object(
                controller.core, "canonical_json_sha256", return_value=SHA
            ),
        ):
            _expect_code(
                lambda: controller._r8u_r6_final_publication_locality(
                    source=source,
                    target=target,
                    implementation_commit=R6_COMMIT,
                    publication_claim=claim,
                    probe=probe,
                    worker_context_diagnostic_sha256=SHA,
                    worker_qstat_projection={"competing_matching_jobs": 0},
                    worker_process_projection={"matching_processes": 0},
                ),
                "R8U_PUBLICATION_TARGET_APPEARED",
            )


def test_claim_probe_final_locality_and_publication_order_is_closed() -> None:
    source = _source(controller.run_r8u_r6_batch16_publication_resume)
    worker = _only_position(source, ("_r8u_r6_build_worker_context(",))
    portable = _only_position(
        source,
        (
            "validate_r8u_r4_portable_candidate_authority(",
            "_r8u_r4_portable_candidate_projection(",
        ),
    )
    claim = _only_position(source, ("_r8u_r6_publication_claim(",))
    claim_write = _only_position(
        source[claim:], ("_r8u_r6_create_publication_claim(",)
    ) + claim
    probe = _only_position(source, ("_r8u_r6_primitive_probe(",))
    final_locality = _only_position(
        source, ("_r8u_r6_final_publication_locality(",)
    )
    publication = _only_position(source, ("_r8u_r6_publish_candidate(",))
    completion = _only_position(
        source,
        (
            "_r8u_r6_complete_batch16_after_publication(",
            "_r8u_r5_complete_batch16_after_publication(",
        ),
    )
    assert (
        worker < portable < claim < claim_write < probe
        < final_locality < publication < completion
    )
    assert "_r8u_r5_live_publication_locality(" not in source
    assert "_r8u_r4_live_publication_locality(" not in source
    publish_source = _source(controller._r8u_r6_publish_candidate)
    assert "final_locality" in inspect.signature(
        controller._r8u_r6_publish_candidate
    ).parameters
    assert "publication_primitive_probe_sha256" in publish_source
    assert "final_publication_locality_sha256" in publish_source


def test_rename_fallback_and_poststate_matrix_remain_closed() -> None:
    source = _source(controller._r8u_r6_publish_candidate)
    assert "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME" in source
    assert "RENAME_NOREPLACE_UNSUPPORTED_EINVAL" in source
    assert "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS" not in source
    assert "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP" not in source
    assert "os.replace(" not in source
    assert "fallback_invoker(" in source
    assert source.count("fallback_invoker(") == 1
    assert source.count("_r8u_r3_raw_rename_noreplace(") <= 1
    for token in (
        "errno.EXDEV",
        "errno.EACCES",
        "errno.EPERM",
        "errno.EEXIST",
        "errno.ENOTEMPTY",
        "errno.EIO",
        "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN",
    ):
        assert token in source
    assert "source_present and target_present" in source
    assert "not source_present and not target_present" in source
    assert "R8U_PUBLICATION_PARENT_MOUNT_CHANGED" in source
    assert "R8U_PUBLICATION_TARGET_APPEARED" in source
    assert "R8U_PUBLICATION_LOCALITY_INVALID" in source


def _publication_fixture(
    root: Path, *, primary_result: str = "RENAME_NOREPLACE_UNSUPPORTED_EINVAL"
) -> tuple[
    Path, Path, Mapping[str, Any], Any,
    controller._R8UR6PublicationLocality, Mapping[str, Any], Mapping[str, Any],
]:
    source = root / "source-parent" / "dicom_extraction"
    target = root / "target-parent" / "dicom_extraction"
    source.mkdir(parents=True, mode=0o700)
    target.parent.mkdir(mode=0o700)
    source.chmod(0o700)
    source.parent.chmod(0o700)
    target.parent.chmod(0o700)
    source_identity = dict(controller._r8u_r4_local_identity(source))
    source_parent_identity = dict(
        controller._r8u_r4_local_identity(source.parent)
    )
    target_parent_identity = dict(
        controller._r8u_r4_local_identity(target.parent)
    )
    locality = controller._R8UR6PublicationLocality(
        value={
            "publication_claim_sha256": SHA,
            "publication_primitive_probe_sha256": SHA,
            "captured_after_primitive_probe": True,
            "probe_cleanup_validated": True,
        },
        source_identity=source_identity,
        source_parent_identity=source_parent_identity,
        target_parent_identity=target_parent_identity,
        source_mount_key=(8,),
        target_mount_key=(8,),
    )
    portable = {
        "candidate_relative_file_portable_projection_sha256": SHA,
        "candidate_npz_files": 1,
        "candidate_total_bytes": 17,
    }
    projection = SimpleNamespace(root_local_identity=source_identity)
    claim = {"status": "AUTHORIZED_EXCLUSIVE_R8U_R6_BATCH16_PUBLICATION"}
    probe = {
        "status": "PASS_R8U_R6_PUBLICATION_PRIMITIVE_PROBE",
        "primary_result": primary_result,
        "probe_cleanup_passed": True,
        "publication_claim_sha256": SHA,
    }
    return source, target, portable, projection, locality, claim, probe


@contextmanager
def _patched_publication() -> Iterator[None]:
    with (
        mock.patch.object(
            controller,
            "validate_r8u_r4_portable_candidate_authority",
            return_value={},
        ),
        mock.patch.object(
            controller,
            "validate_r8u_r6_final_publication_locality",
            return_value={},
        ),
        mock.patch.object(
            controller, "_validate_r8u_r6_publication_claim", return_value={}
        ),
        mock.patch.object(
            controller, "_validate_r8u_r6_primitive_probe", return_value={}
        ),
        mock.patch.object(controller.core, "sha256_file", return_value=SHA),
        mock.patch.object(
            controller.core, "canonical_json_sha256", return_value=SHA
        ),
        mock.patch.object(controller, "_write_private_json", return_value=SHA),
    ):
        yield


def _publish(
    values: tuple[Any, ...],
    *,
    fallback_invoker: Callable[[Path, Path], Any] = os.rename,
    primary_invoker: Callable[[Path, Path], Any] | None = None,
) -> Mapping[str, Any]:
    source, target, portable, projection, locality, claim, probe = values
    return controller._r8u_r6_publish_candidate(
        source=source,
        target=target,
        implementation_commit=R6_COMMIT,
        portable_authority=portable,
        portable_projection=projection,
        final_locality=locality,
        publication_claim=claim,
        probe=probe,
        mount_reader=lambda _path: (8,),
        primary_invoker=primary_invoker,
        fallback_invoker=fallback_invoker,
    )


def test_claim_protected_einval_fallback_renames_exactly_once() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())
        source, target = values[:2]
        calls = 0

        def rename_once(left: Path, right: Path) -> None:
            nonlocal calls
            calls += 1
            os.rename(left, right)

        with (
            _patched_publication(),
            mock.patch.object(
                os, "replace", side_effect=AssertionError("replace reached")
            ),
        ):
            receipt = _publish(values, fallback_invoker=rename_once)
        assert calls == 1
        assert not source.exists() and target.is_dir()
        assert receipt["fallback_used"] is True
        assert receipt["primitive_attempted"] == (
            "CLAIM_PROTECTED_SAME_FILESYSTEM_RENAME"
        )
        assert receipt["publication_attempts"] == 1
        assert receipt["publication_ruling"] == "PUBLICATION_PASS"


def test_supported_primary_renames_once_without_reaching_the_fallback() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(
            Path(temporary).resolve(),
            primary_result="RENAME_NOREPLACE_SUPPORTED",
        )
        primary_calls = 0
        fallback_calls = 0

        def primary(left: Path, right: Path) -> None:
            nonlocal primary_calls
            primary_calls += 1
            os.rename(left, right)

        def fallback(_left: Path, _right: Path) -> None:
            nonlocal fallback_calls
            fallback_calls += 1

        with _patched_publication():
            receipt = _publish(
                values, primary_invoker=primary, fallback_invoker=fallback
            )
        assert primary_calls == 1
        assert fallback_calls == 0
        assert receipt["fallback_used"] is False
        assert receipt["primitive_attempted"] == "RENAMEAT2_RENAME_NOREPLACE"
        assert receipt["publication_attempts"] == 1


def test_ambiguous_nfs_poststate_passes_but_io_and_collision_stay_blocking() -> None:
    for ambiguous_errno in (
        errno.ENOENT,
        getattr(errno, "ESTALE", errno.ENOENT),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            values = _publication_fixture(Path(temporary).resolve())

            def ambiguous(left: Path, right: Path) -> None:
                os.rename(left, right)
                raise OSError(ambiguous_errno, "synthetic ambiguous NFS return")

            with _patched_publication():
                receipt = _publish(values, fallback_invoker=ambiguous)
            assert receipt["rename_returned_success"] is False
            assert receipt["publication_ruling"] == (
                "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN"
            )

    for blocked_errno, expected in (
        (errno.EXDEV, "R8U_PUBLICATION_PARENT_MOUNT_CHANGED"),
        (errno.EACCES, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.EPERM, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.EIO, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.EEXIST, "R8U_PUBLICATION_LOCALITY_INVALID"),
        (errno.ENOTEMPTY, "R8U_PUBLICATION_LOCALITY_INVALID"),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            values = _publication_fixture(Path(temporary).resolve())

            def blocked(_left: Path, _right: Path) -> None:
                raise OSError(blocked_errno, "synthetic blocking return")

            with _patched_publication():
                _expect_code(
                    lambda: _publish(values, fallback_invoker=blocked),
                    expected,
                )


def test_both_present_both_absent_and_non_einval_fallback_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())

        def both_present(_left: Path, right: Path) -> None:
            right.mkdir(mode=0o700)
            raise OSError(errno.EIO, "synthetic")

        with _patched_publication():
            _expect_code(
                lambda: _publish(values, fallback_invoker=both_present),
                "R8U_PUBLICATION_TARGET_APPEARED",
            )

    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())

        def both_absent(left: Path, _right: Path) -> None:
            left.rmdir()
            raise OSError(errno.ENOENT, "synthetic")

        with _patched_publication():
            _expect_code(
                lambda: _publish(values, fallback_invoker=both_absent),
                "R8U_PUBLICATION_LOCALITY_INVALID",
            )

    for unsupported in (
        "RENAME_NOREPLACE_UNSUPPORTED_ENOSYS",
        "RENAME_NOREPLACE_UNSUPPORTED_EOPNOTSUPP",
    ):
        with tempfile.TemporaryDirectory() as temporary:
            values = _publication_fixture(
                Path(temporary).resolve(), primary_result=unsupported
            )
            calls = 0

            def forbidden(_left: Path, _right: Path) -> None:
                nonlocal calls
                calls += 1

            with _patched_publication():
                _expect_code(
                    lambda: _publish(values, fallback_invoker=forbidden),
                    "R8U_R6_PUBLICATION_PRIMITIVE_PROBE_INVALID",
                )
            assert calls == 0


def test_publication_claim_creation_is_exclusive_and_no_clobber() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        parent = root / "batch"
        parent.mkdir(mode=0o700)
        claim_root = parent / ".r8u-r6-claim"
        claim_path = claim_root / "claim.restricted.json"
        with mock.patch.object(controller.core, "sha256_file", return_value=SHA):
            claim = controller._r8u_r6_publication_claim(
                implementation_commit=R6_COMMIT,
                resume_job_id="8123456",
                worker_context_diagnostic_sha256=SHA,
                worker_process_projection={"matching_processes": 0},
                worker_qstat_projection={"competing_matching_jobs": 0},
            )
        with (
            mock.patch.object(
                controller, "R8U_R6_PUBLICATION_CLAIM_ROOT", claim_root
            ),
            mock.patch.object(
                controller, "R8U_R6_PUBLICATION_CLAIM_PATH", claim_path
            ),
        ):
            controller._r8u_r6_create_publication_claim(claim)
            before = claim_path.read_bytes()
            _expect_code(
                lambda: controller._r8u_r6_create_publication_claim(claim),
                "R8U_R6_PUBLICATION_CLAIM_INVALID",
            )
        assert claim_path.read_bytes() == before
        assert stat.S_IMODE(
            claim_path.stat(follow_symlinks=False).st_mode
        ) == 0o600


def test_cpu_probe_qsub_is_single_slot_body_free_and_gpu_free() -> None:
    command = controller._r8u_r6_probe_qsub_command(R6_COMMIT)
    joined = " ".join(command)
    assert "h_rt=00:10:00" in command
    assert command[command.index("-pe") + 1:command.index("-pe") + 3] == [
        "omp", "1",
    ]
    assert "-t" not in command
    assert "gpus=" not in joined
    assert "gpu_" not in joined
    assert "-r n" in joined
    authority_keys = controller.R8U_R6_PROBE_AUTHORITY_KEYS
    assert {
        "candidate_scan_authorized",
        "cloud_requests_authorized",
        "dicom_body_reads_authorized",
        "npz_body_reads_authorized",
        "publication_claim_authorized",
        "real_rename_authorized",
        "extraction_authorized",
        "embedding_generation_authorized",
        "preservation_authorized",
        "scientific_attempt_mutation_authorized",
    } <= authority_keys
    worker_source = _source(controller.run_r8u_r6_locality_sequence_probe)
    receipt_source = (
        _source(controller._r8u_r6_locality_sequence_diagnostic)
        + _source(controller._r8u_r6_probe_receipt)
    )
    calls = "\n".join(_call_names(controller.run_r8u_r6_locality_sequence_probe))
    forbidden = (
        "_r8u_r4_portable_candidate_projection",
        "dependency.dicom",
        "dependency.download",
        "dependency.echoprime",
        "run_production_dicom_extraction",
        "download_mimic_echo_subset",
        "predict",
        "fit",
        "_r8u_r6_publication_claim",
        "_r8u_r6_publish_candidate",
    )
    assert not any(token in calls for token in forbidden), calls
    for field in (
        "candidate_scans", "cloud_requests", "dicom_body_reads",
        "npz_body_reads", "publication_claims", "real_renames",
        "scientific_attempt_mutations",
    ):
        assert field in receipt_source


def test_exactly_two_phase_qsubs_and_one_bounded_cpu_accounting_wait() -> None:
    probe = _source(controller.submit_r8u_r6_locality_sequence_probe)
    adjudicator = _source(controller.adjudicate_r8u_r6_locality_sequence_probe)
    resume = _source(controller.submit_r8u_r6_batch16_publication_resume)
    assert probe.count("scheduler._capture_qsub(") == 1
    assert resume.count("scheduler._capture_qsub(") == 1
    assert probe.count("_r8u_r6_probe_qsub_command(") == 1
    assert resume.count("_r8u_r6_resume_qsub_command(") == 1
    assert _call_names(
        controller.submit_r8u_r6_batch16_publication_resume
    ).count("_r8u_r6_qstat_projection") == 1
    assert "timeout_seconds" in inspect.signature(
        controller.adjudicate_r8u_r6_locality_sequence_probe
    ).parameters
    assert adjudicator.count("_wait_r8u_r6_probe_accounting(") == 1
    assert "R8U_R6_PROBE_ACCOUNTING_PATH" in adjudicator
    assert not _has_while(controller.submit_r8u_r6_locality_sequence_probe)
    assert not _has_while(controller.submit_r8u_r6_batch16_publication_resume)
    combined_calls = (
        _call_names(controller.submit_r8u_r6_locality_sequence_probe)
        + _call_names(controller.submit_r8u_r6_batch16_publication_resume)
    )
    assert not any(call.endswith("time.sleep") for call in combined_calls)
    assert "submit_r8u_r6_continuation_17_19(" not in resume
    assert "run_r8u_r6_continuation_finalizer(" not in resume


def test_gpu_submission_is_one_nonarray_job_with_no_science_on_submitter() -> None:
    command = controller._r8u_r6_resume_qsub_command(R6_COMMIT)
    joined = " ".join(command)
    assert "gpus=1" in command
    assert "-t" not in command
    assert "-r n" in joined
    source = _source(controller.submit_r8u_r6_batch16_publication_resume)
    assert "_r8u_r4_portable_candidate_projection(" not in source
    assert source.index("scheduler._capture_qsub(") < source.index(
        "_r8u_r6_qstat_projection("
    )
    for token in (
        "dependency.dicom(",
        "dependency.download(",
        "dependency.echoprime(",
        "run_production_dicom_extraction",
        "download_mimic_echo_subset",
        "predict(",
        "fit(",
    ):
        assert token not in source.casefold()
    assert {
        "scheduler_submission_count", "resume_is_array", "gpu_requested",
        "automatic_retry_authorized", "cloud_requests", "downloads",
        "dicom_body_reads_by_submitter", "npz_body_reads_by_submitter",
        "dicom_extraction_executions_by_submitter", "model_fitting_count",
        "prediction_generation_count", "confirmatory_performance_access_count",
    } <= controller.R8U_R6_RESUME_SUBMISSION_KEYS


def test_gpu_worker_publication_precedes_echoprime_and_science_is_narrow() -> None:
    worker = _source(controller.run_r8u_r6_batch16_publication_resume)
    completion_function = getattr(
        controller,
        "_r8u_r6_complete_batch16_after_publication",
        controller._r8u_r5_complete_batch16_after_publication,
    )
    completion = _source(completion_function)
    assert worker.index("_r8u_r6_publish_candidate(") < worker.index(
        "_r8u_r5_complete_batch16_after_publication("
        if "_r8u_r5_complete_batch16_after_publication(" in worker
        else "_r8u_r6_complete_batch16_after_publication("
    )
    if completion_function is controller._r8u_r5_complete_batch16_after_publication:
        assert "r8u_r6=True" in worker
    assert "dependency.echoprime(" in completion
    combined = (worker + completion).casefold()
    assert "dependency.dicom(" not in combined
    assert "dependency.download(" not in combined
    assert "run_production_dicom_extraction" not in combined
    assert "download_mimic_echo_subset" not in combined
    assert "predict(" not in combined
    assert "fit(" not in combined
    assert "confirmatory" not in combined


def test_r5_failure_evidence_is_static_and_login_node_body_free() -> None:
    source = _source(controller._r8u_r6_r5_failure_evidence)
    assert "_r8u_r6_read_fixed_r5_scheduler_log(" in source
    assert "_r8u_r6_read_fixed_r5_probe_scheduler_log(" in source
    assert "_r8u_r6_historical_r5_script_authority(" in source
    assert "_r8u_r6_validate_failed_r5_namespace(" in source
    assert "R8U_R5_FAILED_PUBLICATION_RESUME_JOB_ID" in source
    assert "R8U_R5_PASSED_CONTEXT_PROBE_JOB_ID" in source
    assert "R8U_LIVE_PUBLICATION_PARENT_CHANGED" in source
    assert "validate_r8u_r4_portable_candidate_authority(" in source
    assert "_current_r8u_r5_implementation_commit(" not in source
    assert "validate_r8u_r5_resume_terminal(" not in source
    assert "_validate_r8u_r5_probe_accounting(" not in source
    assert "_validate_r8u_r5_resume_submission(" not in source
    assert "_r8u_r5_qstat_projection(" not in source
    assert "_r8u_r5_process_projection(" not in source
    assert "_query_recovery_accounting(" not in source
    assert "_r8u_r4_portable_candidate_projection(" not in source
    assert "candidate_npz_files" in source
    assert "R8U_R3_CANDIDATE_NPZ_FILES" in source
    for name in (
        "R8U_R5_R4_FAILURE_EVIDENCE_PATH",
        "R8U_R5_ACCOUNT_AUTHORITY_PATH",
        "R8U_R5_PROBE_AUTHORITY_PATH",
        "R8U_R5_PROBE_SUBMISSION_PATH",
        "R8U_R5_PROBE_DIAGNOSTIC_PATH",
        "R8U_R5_PROBE_RECEIPT_PATH",
        "R8U_R5_PROBE_ACCOUNTING_PATH",
        "R8U_R5_CAPACITY_PATH",
        "R8U_R5_AUTHORITY_PATH",
        "R8U_R5_SUBMISSION_PATH",
        "R8U_R5_GPU_DIAGNOSTIC_PATH",
        "R8U_R5_LOCALITY_PATH",
        "R8U_R5_PUBLICATION_CLAIM_PATH",
        "R8U_R5_PRIMITIVE_PROBE_PATH",
    ):
        assert name in source


def test_static_r5_common_and_worker_diagnostics_reject_tampering() -> None:
    account = {
        **controller._r8u_r5_common(
            artifact_type="lvef_c3_r8u_r5_scheduler_account_authority_v1",
            status="AUTHORIZED_R8U_R5_SCHEDULER_ACCOUNT",
            implementation_commit=R5_COMMIT,
        ),
        **{
            key: None
            for key in (
                controller.R8U_R5_ACCOUNT_AUTHORITY_KEYS
                - controller.R8U_R5_COMMON_KEYS
            )
        },
    }
    controller._r8u_r6_require_static_r5_common(
        account,
        keys=controller.R8U_R5_ACCOUNT_AUTHORITY_KEYS,
        artifact_type="lvef_c3_r8u_r5_scheduler_account_authority_v1",
        status="AUTHORIZED_R8U_R5_SCHEDULER_ACCOUNT",
    )
    for field, replacement in (
        ("implementation_commit", "d" * 40),
        ("batch_id", "c3_batch_014"),
    ):
        tampered = dict(account)
        tampered[field] = replacement
        _expect_code(
            lambda tampered=tampered:
                controller._r8u_r6_require_static_r5_common(
                    tampered,
                    keys=controller.R8U_R5_ACCOUNT_AUTHORITY_KEYS,
                    artifact_type=(
                        "lvef_c3_r8u_r5_scheduler_account_authority_v1"
                    ),
                    status="AUTHORIZED_R8U_R5_SCHEDULER_ACCOUNT",
                ),
            "R8U_R6_R5_FAILURE_EVIDENCE_INVALID",
        )
    unexpected = dict(account)
    unexpected["unexpected"] = True
    _expect_code(
        lambda: controller._r8u_r6_require_static_r5_common(
            unexpected,
            keys=controller.R8U_R5_ACCOUNT_AUTHORITY_KEYS,
            artifact_type="lvef_c3_r8u_r5_scheduler_account_authority_v1",
            status="AUTHORIZED_R8U_R5_SCHEDULER_ACCOUNT",
        ),
        "R8U_R6_R5_FAILURE_EVIDENCE_INVALID",
    )

    diagnostic = {
        key: True
        for key in controller.R8U_R5_WORKER_DIAGNOSTIC_KEYS
    }
    diagnostic.update({
        "schema_version": 1,
        "artifact_type": "lvef_c3_r8u_r5_worker_scheduler_context_v1",
        "status": "PASS_R8U_R5_WORKER_SCHEDULER_CONTEXT",
        "observed_shell_match": False,
        "passwd_lookup_status": "PASS",
        "classifications": ["SCHEDULER_ENV_SHELL_MISMATCH"],
        "canonical_worker_environment_status": "PASS",
    })
    controller._r8u_r6_validate_static_r5_worker_diagnostic(
        diagnostic, fixed_cpu_probe=True
    )
    for field, replacement in (
        ("job_id_match", False),
        ("observed_shell_match", True),
        ("classifications", ["PASS"]),
    ):
        tampered = dict(diagnostic)
        tampered[field] = replacement
        _expect_code(
            lambda tampered=tampered:
                controller._r8u_r6_validate_static_r5_worker_diagnostic(
                    tampered, fixed_cpu_probe=True
                ),
            "R8U_R6_R5_FAILURE_EVIDENCE_INVALID",
        )


def test_static_r5_qsub_qstat_and_cpu_log_reject_tampering() -> None:
    qstat_body = {
        "status": "PASS_EXACT_ONE_R8U_R5_SUBMITTED_JOB_ZERO_COMPETITORS",
        "resume_job_id": "7388079",
        "resume_job_name": "lvef_c3_r8u_r5_res_7b7c3657",
        "state": "qw",
        "category": "pending",
        "target_matches": 1,
        "competing_matching_jobs": 0,
        "qstat_snapshot_count": 1,
    }
    qstat = {
        **qstat_body,
        "qstat_projection_sha256": controller.core.canonical_json_sha256(
            qstat_body
        ),
    }
    controller._r8u_r6_validate_static_r5_qstat_projection(
        qstat,
        job_id="7388079",
        job_name="lvef_c3_r8u_r5_res_7b7c3657",
    )
    tampered_qstat = dict(qstat)
    tampered_qstat["competing_matching_jobs"] = 1
    _expect_code(
        lambda: controller._r8u_r6_validate_static_r5_qstat_projection(
            tampered_qstat,
            job_id="7388079",
            job_name="lvef_c3_r8u_r5_res_7b7c3657",
        ),
        "R8U_R6_R5_FAILURE_EVIDENCE_INVALID",
    )

    with tempfile.TemporaryDirectory() as temporary:
        scheduler_root = Path(temporary).resolve()
        for label, job_id in (("probe", "7387915"), ("resume", "7388079")):
            for kind, payload in (
                ("stdout", f"{job_id}\n".encode("ascii")),
                ("stderr", b""),
                ("exit_status", b"0\n"),
            ):
                evidence_path = scheduler_root / f"{label}.qsub.{kind}.restricted"
                evidence_path.write_bytes(payload)
                evidence_path.chmod(0o600)
        probe_log = (
            scheduler_root
            / controller.R8U_R5_PASSED_CONTEXT_PROBE_LOG_BASENAME
        )
        probe_log.write_bytes(controller.R8U_R5_PASSED_CONTEXT_PROBE_LOG_PAYLOAD)
        probe_log.chmod(0o600)
        with mock.patch.object(
            controller, "R8U_R5_SCHEDULER_ROOT", scheduler_root
        ):
            probe_evidence = controller._r8u_r6_read_static_r5_qsub_evidence(
                label="probe", expected_job_id="7387915"
            )
            assert probe_evidence["exit_status"] == 0
            payload, digest = (
                controller._r8u_r6_read_fixed_r5_probe_scheduler_log()
            )
            assert payload == controller.R8U_R5_PASSED_CONTEXT_PROBE_LOG_PAYLOAD
            assert digest == controller._sha256_bytes(payload)

            (scheduler_root / "probe.qsub.stdout.restricted").write_bytes(
                b"7387916\n"
            )
            _expect_code(
                lambda: controller._r8u_r6_read_static_r5_qsub_evidence(
                    label="probe", expected_job_id="7387915"
                ),
                "R8U_R6_R5_FAILURE_EVIDENCE_INVALID",
            )
            (scheduler_root / "probe.qsub.stdout.restricted").chmod(0o600)
            probe_log.write_bytes(
                controller.R8U_R5_PASSED_CONTEXT_PROBE_LOG_PAYLOAD[:-1]
            )
            probe_log.chmod(0o600)
            _expect_code(
                controller._r8u_r6_read_fixed_r5_probe_scheduler_log,
                "R8U_R6_R5_FAILURE_EVIDENCE_INVALID",
            )


def test_r5_historical_script_authority_is_bound_to_git_blobs() -> None:
    assert controller._r8u_r6_historical_r5_script_authority() == dict(
        sorted(controller.R8U_R5_HISTORICAL_SCRIPT_AUTHORITY.items())
    )
    tampered = dict(controller.R8U_R5_HISTORICAL_SCRIPT_AUTHORITY)
    tampered["runner_sha256"] = "b" * 64
    with mock.patch.object(
        controller, "R8U_R5_HISTORICAL_SCRIPT_AUTHORITY", tampered
    ):
        _expect_code(
            controller._r8u_r6_historical_r5_script_authority,
            "R8U_R6_R5_FAILURE_EVIDENCE_INVALID",
        )


def test_r6_continuation_and_finalizer_consume_the_new_terminal_epoch() -> None:
    continuation = _source(controller.validate_r8u_r6_continuation_worker_submission)
    assert "validate_r8u_r6_resume_terminal(" in continuation or (
        "R8U_R6_TERMINAL_PATH" in continuation
    )
    assert "_r8u_r6_build_worker_context(" in continuation
    assert "scheduler_account_authority_sha256" in continuation
    sequential_source = _source(controller.sequential.run_batch_task)
    assert "R8U_R6_FIXED_CONTINUATION" in sequential_source
    assert "validate_r8u_r6_continuation_worker_submission(" in sequential_source
    assert hasattr(finalizer, "R8UR6ImplementationAuthority")
    assert "r8u_r6_implementation_authority" in inspect.signature(
        finalizer.finalize_receipts
    ).parameters
    assert callable(finalizer._validate_r8u_r6_mixed_implementation_epochs)
    future_submit = _source(controller.submit_r8u_r6_continuation_17_19)
    assert "validate_r8u_r6_resume_terminal(" in future_submit
    assert future_submit.count("scheduler._capture_qsub(") == 2
    resume_submit = _source(controller.submit_r8u_r6_batch16_publication_resume)
    assert "submit_r8u_r6_continuation_17_19(" not in resume_submit
    future_finalizer = _source(controller.run_r8u_r6_continuation_finalizer)
    assert "R8UR6ImplementationAuthority(" in future_finalizer
    assert "r8u_r6_implementation_authority=" in future_finalizer


def test_r6_finalizer_mirrors_each_materialized_controller_schema() -> None:
    pairs = (
        ("R8U_R6_ACCOUNT_AUTHORITY_KEYS", "R8U_R6_ACCOUNT_AUTHORITY_KEYS"),
        ("R8U_R6_R5_FAILURE_EVIDENCE_KEYS", "R8U_R6_R5_FAILURE_EVIDENCE_KEYS"),
        ("R8U_R6_PROBE_AUTHORITY_KEYS", "R8U_R6_PROBE_AUTHORITY_KEYS"),
        ("R8U_R6_PROBE_SUBMISSION_KEYS", "R8U_R6_PROBE_SUBMISSION_KEYS"),
        ("R8U_R6_PROBE_DIAGNOSTIC_KEYS", "R8U_R6_PROBE_DIAGNOSTIC_KEYS"),
        ("R8U_R6_PROBE_RECEIPT_KEYS", "R8U_R6_PROBE_RECEIPT_KEYS"),
        ("R8U_R6_PROBE_ACCOUNTING_KEYS", "R8U_R6_PROBE_ACCOUNTING_KEYS"),
        ("R8U_R6_CAPACITY_KEYS", "R8U_R6_CAPACITY_KEYS"),
        ("R8U_R6_AUTHORITY_KEYS", "R8U_R6_RESUME_AUTHORITY_KEYS"),
        ("R8U_R6_SUBMISSION_KEYS", "R8U_R6_RESUME_SUBMISSION_KEYS"),
        ("R8U_R6_PUBLICATION_CLAIM_KEYS", "R8U_R6_PUBLICATION_CLAIM_KEYS"),
        ("R8U_R6_PROBE_KEYS", "R8U_R6_PRIMITIVE_PROBE_KEYS"),
        ("R8U_R6_FINAL_LOCALITY_KEYS", "R8U_R6_FINAL_LOCALITY_KEYS"),
        ("R8U_R6_PUBLICATION_KEYS", "R8U_R6_PUBLICATION_KEYS"),
        ("R8U_R6_ACCOUNTING_KEYS", "R8U_R6_RESUME_ACCOUNTING_KEYS"),
        ("R8U_R6_TERMINAL_KEYS", "R8U_R6_TERMINAL_KEYS"),
    )
    for finalizer_name, controller_name in pairs:
        assert getattr(finalizer, finalizer_name) == getattr(
            controller, controller_name
        ), (finalizer_name, controller_name)


def test_r6_receipts_bind_claim_probe_locality_and_publish_once() -> None:
    for keys in (
        controller.R8U_R6_PRIMITIVE_PROBE_KEYS,
        controller.R8U_R6_FINAL_LOCALITY_KEYS,
        controller.R8U_R6_PUBLICATION_KEYS,
        controller.R8U_R6_TERMINAL_KEYS,
    ):
        assert "scheduler_account_authority_sha256" in keys
    assert "publication_claim_sha256" in controller.R8U_R6_PRIMITIVE_PROBE_KEYS
    assert {
        "publication_claim_sha256",
        "publication_primitive_probe_sha256",
    } <= controller.R8U_R6_FINAL_LOCALITY_KEYS
    assert {
        "publication_claim_sha256",
        "publication_primitive_probe_sha256",
        "final_publication_locality_sha256",
        "publication_attempts",
    } <= controller.R8U_R6_PUBLICATION_KEYS
    assert {
        "publication_receipt_sha256",
        "final_publication_locality_sha256",
    } <= controller.R8U_R6_TERMINAL_KEYS


def test_r6_readback_validators_reject_common_and_derived_field_tampering() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        diagnostic = _run_locality_sequence(root=Path(temporary).resolve())
    with (
        mock.patch.object(
            controller, "_current_r8u_r6_implementation_commit",
            return_value=R6_COMMIT,
        ),
        mock.patch.object(controller.core, "sha256_file", return_value=SHA),
    ):
        assert controller._validate_r8u_r6_probe_diagnostic(
            diagnostic, probe_job_id="8123456"
        ) is diagnostic
        tampered_common = dict(diagnostic)
        tampered_common["attempt_id"] = "tampered"
        _expect_code(
            lambda: controller._validate_r8u_r6_probe_diagnostic(
                tampered_common, probe_job_id="8123456"
            ),
            "R8U_R6_PROBE_DIAGNOSTIC_INVALID",
        )
        tampered_errno = dict(diagnostic)
        tampered_errno["primary_errno"] = "NONE"
        _expect_code(
            lambda: controller._validate_r8u_r6_probe_diagnostic(
                tampered_errno, probe_job_id="8123456"
            ),
            "R8U_R6_PROBE_DIAGNOSTIC_INVALID",
        )
        tampered_change = dict(diagnostic)
        tampered_change["volatile_changed_fields"] = ["source.nlink"]
        tampered_change["volatile_metadata_classification"] = (
            "R8U_PUBLICATION_VOLATILE_METADATA_ONLY_CHANGED"
        )
        _expect_code(
            lambda: controller._validate_r8u_r6_probe_diagnostic(
                tampered_change, probe_job_id="8123456"
            ),
            "R8U_R6_PROBE_DIAGNOSTIC_INVALID",
        )

    probe = {
        **controller._r8u_r6_common(
            artifact_type="lvef_c3_r8u_r6_publication_primitive_probe_v1",
            status="PASS_R8U_R6_PUBLICATION_PRIMITIVE_PROBE",
            implementation_commit=R6_COMMIT,
        ),
        "scheduler_account_authority_sha256": SHA,
        "publication_claim_sha256": SHA,
        "primary_primitive": "RENAMEAT2_RENAME_NOREPLACE",
        "primary_result": "RENAME_NOREPLACE_UNSUPPORTED_EINVAL",
        "primary_errno": "EINVAL",
        "primary_errno_number": errno.EINVAL,
        "primary_returned_success": False,
        "probe_source_present_after": True,
        "probe_target_present_after": False,
        "probe_target_exact_after": False,
        "probe_cleanup_passed": True,
        "probe_directories_created": 2,
        "probe_directories_removed": 2,
        "scientific_file_body_reads": 0,
        "npz_body_reads": 0,
        "dicom_body_reads": 0,
        "dicom_extraction_executions": 0,
    }
    with (
        mock.patch.object(
            controller, "_current_r8u_r6_implementation_commit",
            return_value=R6_COMMIT,
        ),
        mock.patch.object(controller.core, "sha256_file", return_value=SHA),
    ):
        assert controller._validate_r8u_r6_primitive_probe(probe) is probe
        tampered_probe = dict(probe)
        tampered_probe["primary_errno_number"] = 0
        _expect_code(
            lambda: controller._validate_r8u_r6_primitive_probe(tampered_probe),
            "R8U_R6_PUBLICATION_PRIMITIVE_PROBE_INVALID",
        )

    with tempfile.TemporaryDirectory() as temporary:
        locality = _run_final_locality(root=Path(temporary).resolve()).value
    claim = {
        "worker_context_diagnostic_sha256": SHA,
        "worker_qstat_projection_sha256": SHA,
        "worker_process_projection_sha256": SHA,
    }
    with (
        mock.patch.object(
            controller, "_current_r8u_r6_implementation_commit",
            return_value=R6_COMMIT,
        ),
        mock.patch.object(controller.core, "sha256_file", return_value=SHA),
        mock.patch.object(
            controller, "_validate_r8u_r6_publication_claim",
            return_value=claim,
        ),
        mock.patch.object(
            controller, "_validate_r8u_r6_primitive_probe", return_value=probe
        ),
    ):
        assert controller.validate_r8u_r6_final_publication_locality(
            locality
        ) is locality
        tampered_locality = dict(locality)
        tampered_locality["source_volatile_fields_equal"] = dict(
            locality["source_volatile_fields_equal"]
        )
        tampered_locality["source_volatile_fields_equal"]["nlink"] = False
        _expect_code(
            lambda: controller.validate_r8u_r6_final_publication_locality(
                tampered_locality
            ),
            "R8U_PUBLICATION_LOCALITY_INVALID",
        )

    with tempfile.TemporaryDirectory() as temporary:
        values = _publication_fixture(Path(temporary).resolve())
        with _patched_publication():
            publication = _publish(values)
    publication = dict(publication)
    publication["candidate_npz_files"] = controller.R8U_R3_CANDIDATE_NPZ_FILES
    publication["files_moved"] = controller.R8U_R3_CANDIDATE_NPZ_FILES
    portable = dict(values[2])
    portable["candidate_npz_files"] = controller.R8U_R3_CANDIDATE_NPZ_FILES
    with (
        mock.patch.object(
            controller, "_current_r8u_r6_implementation_commit",
            return_value=R6_COMMIT,
        ),
        mock.patch.object(controller.core, "sha256_file", return_value=SHA),
        mock.patch.object(
            controller, "_validate_r8u_r6_primitive_probe", return_value=probe
        ),
        mock.patch.object(
            controller, "validate_r8u_r6_final_publication_locality",
            return_value={"source_stable_identity_sha256": SHA},
        ),
        mock.patch.object(
            controller, "validate_r8u_r4_portable_candidate_authority",
            return_value=portable,
        ),
    ):
        assert controller.validate_r8u_r6_publication(publication) is publication
        for field, replacement in (
            ("primitive_attempted", "RENAMEAT2_RENAME_NOREPLACE"),
            ("prepublication_candidate_sha256", "b" * 64),
            ("publication_ruling", "PUBLICATION_PASS_AFTER_AMBIGUOUS_NFS_RETURN"),
        ):
            tampered_publication = dict(publication)
            tampered_publication[field] = replacement
            _expect_code(
                lambda tampered_publication=tampered_publication:
                    controller.validate_r8u_r6_publication(tampered_publication),
                "R8U_R6_PUBLICATION_RECEIPT_INVALID",
            )


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
