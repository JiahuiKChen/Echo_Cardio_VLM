#!/usr/bin/env python3
"""Focused dependency-light R8U-R3 controller and runner tests."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, redirect_stdout
import errno
import io
import inspect
import json
import os
from pathlib import Path, PurePosixPath
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


def test_metadata_candidate_projection_opens_no_npz_and_rejects_extra() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "dicom_extraction"
        root.mkdir(mode=0o700)
        for name in controller.R8U_R3_CANDIDATE_CONTROL_FILES:
            path = root / name
            path.write_bytes(b"control\n")
            path.chmod(0o600)
        npz = root / "clips" / "aa" / f"{'a' * 64}.npz"
        npz.parent.mkdir(parents=True, mode=0o700)
        npz.parent.parent.chmod(0o700)
        npz.parent.chmod(0o700)
        npz.write_bytes(b"opaque-scientific-body")
        npz.chmod(0o600)
        expected = frozenset(
            {PurePosixPath(f"clips/aa/{'a' * 64}.npz")}
        )
        with mock.patch.object(controller, "R8U_R3_CANDIDATE_NPZ_FILES", 1):
            projection = controller._r8u_r3_metadata_projection(
                root, expected_npz_paths=expected
            )
            assert projection.value["candidate_npz_files"] == 1
            extra = root / "clips" / "bb" / f"{'b' * 64}.npz"
            extra.parent.mkdir(mode=0o700)
            extra.write_bytes(b"extra")
            extra.chmod(0o600)
            _expect_code(
                lambda: controller._r8u_r3_metadata_projection(
                    root, expected_npz_paths=expected
                ),
                "R8U_PUBLICATION_SOURCE_AUTHORITY_INVALID",
            )


def test_metadata_projection_detects_control_and_npz_substitution() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve() / "dicom_extraction"
        root.mkdir(mode=0o700)
        for name in controller.R8U_R3_CANDIDATE_CONTROL_FILES:
            path = root / name
            path.write_bytes(b"control\n")
            path.chmod(0o600)
        relative = PurePosixPath(f"clips/aa/{'a' * 64}.npz")
        npz = root / relative
        npz.parent.mkdir(parents=True, mode=0o700)
        npz.parent.parent.chmod(0o700)
        npz.parent.chmod(0o700)
        npz.write_bytes(b"opaque-body")
        npz.chmod(0o600)
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
                "R8U_PUBLICATION_SOURCE_AUTHORITY_INVALID",
            )
            renamed.unlink()
            _expect_code(
                lambda: controller._r8u_r3_metadata_projection(
                    root, expected_npz_paths=frozenset({relative})
                ),
                "R8U_PUBLICATION_SOURCE_AUTHORITY_INVALID",
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
        events.append(
            "write_submission"
            if path == controller.R8U_R3_SUBMISSION_PATH else "write_control"
        )
        return candidate_sha if path == controller.R8U_R3_CANDIDATE_SEAL_PATH else SHA

    def qsub(*_args, **_kwargs) -> str:
        events.append("qsub")
        return "8123456"

    def qstat(**_kwargs):
        events.append("qstat")
        return qstat_projection

    capacity_value = {"status": controller.R8U_R3_CAPACITY_STATUS}
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
                return_value=capacity_value,
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
    assert events.count("qsub") == 1
    assert events.count("qstat") == 1
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
