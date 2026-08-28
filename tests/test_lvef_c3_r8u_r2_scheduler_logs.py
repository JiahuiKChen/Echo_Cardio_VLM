from __future__ import annotations

"""Dependency-light contracts for R8U-R2 Grid Engine log authority."""

from contextlib import ExitStack, contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Iterator
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_r8r_recovery_continuation as r8u


def _expect_code(call: Any, expected: str) -> None:
    try:
        call()
    except r8u.R8RControllerError as caught:
        assert caught.code == expected
    else:
        raise AssertionError(f"expected {expected}")


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _write_mode(path: Path, payload: bytes, mode: int) -> None:
    _private_directory(path.parent)
    path.write_bytes(payload)
    path.chmod(mode)


def _binding(
    attempt: Path,
    *,
    role: str,
    job_id: str = "8123456",
    task_id: str = "NONE",
) -> r8u._R8USchedulerLogBinding:
    if role == "FAILED_R8U_BATCH16_RECOVERY":
        scheduler_root = attempt / "r8u_batch16_recovery" / "scheduler"
        _private_directory(scheduler_root.parent)
        _private_directory(scheduler_root)
        return r8u._R8USchedulerLogBinding(
            role=role,
            scheduler_root=scheduler_root,
            job_name=r8u.R8U_FAILED_RECOVERY_JOB_NAME,
            job_id=r8u.R8U_FAILED_RECOVERY_JOB_ID,
            task_id="NONE",
            terminal_state="TERMINAL_FAILED_APPLICATION_EXIT_78",
        )
    if role == "FRESH_R8U_R2_BATCH16_RECOVERY":
        kind = "rec"
        scheduler_root = attempt / "r8u_r2_batch16_recovery" / "scheduler"
    elif role == "R8U_R2_CONTINUATION_ARRAY_TASK":
        kind = "seq"
        scheduler_root = attempt / "r8u_r2_continuation_17_19" / "scheduler"
    else:
        kind = "fin"
        scheduler_root = attempt / "r8u_r2_continuation_17_19" / "scheduler"
    _private_directory(scheduler_root.parent)
    _private_directory(scheduler_root)
    return r8u._R8USchedulerLogBinding(
        role=role,
        scheduler_root=scheduler_root,
        job_name=f"lvef_c3_r8u_{kind}_abcdef12",
        job_id=job_id,
        task_id=task_id,
        terminal_state="SUBMITTED_OR_RUNNING",
    )


@contextmanager
def _attempt() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
        attempt = Path(temporary).resolve()
        attempt.chmod(0o700)
        for patcher in (
            mock.patch.object(r8u, "ATTEMPT_ROOT", attempt),
            mock.patch.object(
                r8u, "_r8u_nested_mount_paths", return_value=frozenset()
            ),
            mock.patch.object(r8u.os.path, "ismount", return_value=False),
        ):
            stack.enter_context(patcher)
        yield attempt


@contextmanager
def _patched_failed_payload(payload: bytes) -> Iterator[None]:
    relative = (
        "scheduler/"
        f"{r8u.R8U_FAILED_RECOVERY_JOB_NAME}.o"
        f"{r8u.R8U_FAILED_RECOVERY_JOB_ID}"
    )
    authorities = dict(r8u.R8U_FAILED_RECOVERY_FILE_AUTHORITIES)
    authorities[relative] = (len(payload), hashlib.sha256(payload).hexdigest())
    with mock.patch.object(
        r8u, "R8U_FAILED_RECOVERY_FILE_AUTHORITIES", authorities
    ):
        yield


def _read_evidence(
    attempt: Path,
    binding: r8u._R8USchedulerLogBinding,
    *,
    payload: bytes = b"scheduler evidence\n",
    mode: int = 0o600,
) -> dict[str, Any]:
    _private_directory(binding.scheduler_root.parent)
    _private_directory(binding.scheduler_root)
    path = attempt / Path(
        r8u._r8u_scheduler_log_relative_path(binding).as_posix()
    )
    _write_mode(path, payload, mode)
    return dict(
        r8u._r8u_scheduler_log_evidence(
            path=path,
            binding=binding,
            approved_device=attempt.stat().st_dev,
        )
    )


def test_exact_failed_log_role_accepts_only_0600_or_0644() -> None:
    payload = b"sealed failed scheduler evidence\n"
    for mode in (0o600, 0o644):
        with _attempt() as attempt, _patched_failed_payload(payload):
            evidence = _read_evidence(
                attempt,
                _binding(attempt, role="FAILED_R8U_BATCH16_RECOVERY"),
                payload=payload,
                mode=mode,
            )
            assert evidence == {
                "artifact_type": "lvef_c3_r8u_r2_scheduler_log_evidence_v1",
                "status": (
                    "PASS_ROLE_BOUND_GRID_ENGINE_MERGED_STDOUT_STDERR_LOG"
                ),
                "evidence_class": (
                    "GRID_ENGINE_MERGED_SCHEDULER_EVIDENCE"
                ),
                "role": "FAILED_R8U_BATCH16_RECOVERY",
                "basename": (
                    f"{r8u.R8U_FAILED_RECOVERY_JOB_NAME}.o"
                    f"{r8u.R8U_FAILED_RECOVERY_JOB_ID}"
                ),
                "job_id": r8u.R8U_FAILED_RECOVERY_JOB_ID,
                "task_id": "NONE",
                "mode": f"{mode:04o}",
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "owner_uid": os.geteuid(),
                "terminal_state": "TERMINAL_FAILED_APPLICATION_EXIT_78",
            }

    with _attempt() as attempt, _patched_failed_payload(payload):
        binding = _binding(attempt, role="FAILED_R8U_BATCH16_RECOVERY")
        _expect_code(
            lambda: _read_evidence(
                attempt, binding, payload=payload, mode=0o640
            ),
            "R8U_SCHEDULER_LOG_AUTHORITY_INVALID",
        )


def test_public_nonlog_files_remain_forbidden_in_every_successor_role() -> None:
    labels = (
        "scientific.dcm",
        "control.restricted.json",
        "claim.restricted.json",
        "capacity.restricted.json",
        "receipt.restricted.json",
        "clip.npz",
        "embedding.npy",
        "arbitrary.bin",
    )
    for label in labels:
        with _attempt() as attempt:
            _private_directory(attempt / "protected")
            _write_mode(attempt / "protected" / "sealed", b"x", 0o600)
            fixed = frozenset({PurePosixPath("."), PurePosixPath("protected")})
            excluded = frozenset({PurePosixPath("successor")})
            with ExitStack() as stack:
                for patcher in (
                    mock.patch.object(
                        r8u, "R8U_FIXED_REQUIRED_DIRECTORY_PATHS", fixed
                    ),
                    mock.patch.object(
                        r8u, "R8U_PROTECTED_DIRECTORY_ROLE_ROOTS", fixed
                    ),
                    mock.patch.object(
                        r8u, "R8U_SUCCESSOR_EXCLUSION_PATHS", excluded
                    ),
                ):
                    stack.enter_context(patcher)
                _write_mode(attempt / "successor" / label, b"x", 0o644)
                _expect_code(
                    r8u._r8u_scan_attempt_content,
                    "R8U_UNCLASSIFIED_PUBLIC_FILE_IN_SUCCESSOR_NAMESPACE",
                )


def test_scheduler_log_topology_size_and_binding_failures_are_specific() -> None:
    for mode in (0o620, 0o602, 0o610, 0o601, 0o666, 0o755):
        with _attempt() as attempt:
            binding = _binding(
                attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY"
            )
            _expect_code(
                lambda: _read_evidence(attempt, binding, mode=mode),
                "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID",
            )

    with _attempt() as attempt:
        binding = _binding(attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY")
        path = attempt / Path(
            r8u._r8u_scheduler_log_relative_path(binding).as_posix()
        )
        _write_mode(path, b"x", 0o600)
        os.link(path, path.with_name("hardlink"))
        _expect_code(
            lambda: r8u._r8u_scheduler_log_evidence(
                path=path,
                binding=binding,
                approved_device=attempt.stat().st_dev,
            ),
            "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID",
        )

    with _attempt() as attempt:
        binding = _binding(attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY")
        path = attempt / Path(
            r8u._r8u_scheduler_log_relative_path(binding).as_posix()
        )
        _private_directory(path.parent)
        target = path.with_name("target")
        _write_mode(target, b"x", 0o600)
        path.symlink_to(target)
        _expect_code(
            lambda: r8u._r8u_scheduler_log_evidence(
                path=path,
                binding=binding,
                approved_device=attempt.stat().st_dev,
            ),
            "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID",
        )

    with _attempt() as attempt:
        binding = _binding(attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY")
        path = attempt / Path(
            r8u._r8u_scheduler_log_relative_path(binding).as_posix()
        )
        _private_directory(path.parent)
        os.mkfifo(path, 0o600)
        _expect_code(
            lambda: r8u._r8u_scheduler_log_evidence(
                path=path,
                binding=binding,
                approved_device=attempt.stat().st_dev,
            ),
            "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID",
        )

    with _attempt() as attempt:
        binding = _binding(attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY")
        path = attempt / Path(
            r8u._r8u_scheduler_log_relative_path(binding).as_posix()
        )
        _write_mode(path, b"", 0o600)
        os.truncate(path, r8u.R8U_SCHEDULER_LOG_MAX_BYTES + 1)
        _expect_code(
            lambda: r8u._r8u_scheduler_log_evidence(
                path=path,
                binding=binding,
                approved_device=attempt.stat().st_dev,
            ),
            "R8U_SCHEDULER_LOG_SIZE_INVALID",
        )

    with _attempt() as attempt:
        binding = _binding(attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY")
        expected = attempt / Path(
            r8u._r8u_scheduler_log_relative_path(binding).as_posix()
        )
        wrong = expected.with_name("wrong.o8123456")
        _write_mode(wrong, b"x", 0o600)
        _expect_code(
            lambda: r8u._r8u_scheduler_log_evidence(
                path=wrong,
                binding=binding,
                approved_device=attempt.stat().st_dev,
            ),
            "R8U_SCHEDULER_LOG_JOB_BINDING_INVALID",
        )

    with _attempt() as attempt, ExitStack() as stack:
        _private_directory(attempt / "protected")
        _write_mode(attempt / "protected" / "sealed", b"science", 0o600)
        binding = _binding(
            attempt,
            role="FRESH_R8U_R2_BATCH16_RECOVERY",
            job_id="8123456",
        )
        expected_relative = r8u._r8u_scheduler_log_relative_path(binding)
        wrong_job_path = binding.scheduler_root / (
            f"{binding.job_name}.o8123457"
        )
        _write_mode(wrong_job_path, b"unbound job\n", 0o600)
        fixed = frozenset({PurePosixPath("."), PurePosixPath("protected")})
        for patcher in (
            mock.patch.object(
                r8u, "R8U_FIXED_REQUIRED_DIRECTORY_PATHS", fixed
            ),
            mock.patch.object(
                r8u, "R8U_PROTECTED_DIRECTORY_ROLE_ROOTS", fixed
            ),
            mock.patch.object(
                r8u,
                "R8U_SUCCESSOR_EXCLUSION_PATHS",
                frozenset({PurePosixPath("r8u_r2_batch16_recovery")}),
            ),
            mock.patch.object(
                r8u,
                "_r8u_scheduler_log_bindings",
                return_value={expected_relative: binding},
            ),
        ):
            stack.enter_context(patcher)
        _expect_code(
            r8u._r8u_scan_attempt_content,
            "R8U_SCHEDULER_LOG_JOB_BINDING_INVALID",
        )


def test_wrong_owner_and_identity_mutation_fail_closed() -> None:
    with _attempt() as attempt:
        binding = _binding(attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY")
        path = attempt / Path(
            r8u._r8u_scheduler_log_relative_path(binding).as_posix()
        )
        _write_mode(path, b"x", 0o600)
        real_fstat = os.fstat

        def wrong_owner(descriptor: int) -> Any:
            value = real_fstat(descriptor)
            return SimpleNamespace(
                st_mode=value.st_mode,
                st_uid=value.st_uid + 1,
                st_gid=value.st_gid,
                st_dev=value.st_dev,
                st_ino=value.st_ino,
                st_nlink=value.st_nlink,
                st_size=value.st_size,
                st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns,
            )

        with mock.patch.object(r8u.os, "fstat", side_effect=wrong_owner):
            _expect_code(
                lambda: r8u._r8u_scheduler_log_evidence(
                    path=path,
                    binding=binding,
                    approved_device=attempt.stat().st_dev,
                ),
                "R8U_SCHEDULER_LOG_AUTHORITY_INVALID",
            )


def test_cross_device_special_mount_and_ancestor_alias_fail_closed() -> None:
    def changed(value: Any, **updates: Any) -> SimpleNamespace:
        fields = {
            name: getattr(value, name)
            for name in (
                "st_mode",
                "st_uid",
                "st_gid",
                "st_dev",
                "st_ino",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        fields.update(updates)
        return SimpleNamespace(**fields)

    for mutation in ("cross_device", "special", "socket"):
        with _attempt() as attempt:
            binding = _binding(
                attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY"
            )
            path = attempt / Path(
                r8u._r8u_scheduler_log_relative_path(binding).as_posix()
            )
            _write_mode(path, b"x", 0o600)
            real_fstat = os.fstat

            def fake_fstat(descriptor: int) -> Any:
                value = real_fstat(descriptor)
                if mutation == "cross_device":
                    return changed(value, st_dev=value.st_dev + 1)
                special_type = (
                    stat.S_IFSOCK if mutation == "socket" else stat.S_IFCHR
                )
                return changed(value, st_mode=special_type | 0o600)

            with mock.patch.object(
                r8u.os, "fstat", side_effect=fake_fstat
            ):
                _expect_code(
                    lambda: r8u._r8u_scheduler_log_evidence(
                        path=path,
                        binding=binding,
                        approved_device=attempt.stat().st_dev,
                    ),
                    "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID",
                )

    with _attempt() as attempt:
        binding = _binding(attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY")
        path = attempt / Path(
            r8u._r8u_scheduler_log_relative_path(binding).as_posix()
        )
        _write_mode(path, b"x", 0o600)
        real_lstat = r8u._r8u_stable_lstat
        leaf_reads = 0

        def unstable(candidate: Path) -> Any:
            nonlocal leaf_reads
            value = real_lstat(candidate)
            if candidate == path:
                leaf_reads += 1
                if leaf_reads >= 2:
                    return changed(value, st_ctime_ns=value.st_ctime_ns + 1)
            return value

        with mock.patch.object(
            r8u, "_r8u_stable_lstat", side_effect=unstable
        ):
            _expect_code(
                lambda: r8u._r8u_scheduler_log_evidence(
                    path=path,
                    binding=binding,
                    approved_device=attempt.stat().st_dev,
                ),
                "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID",
            )

    with _attempt() as attempt:
        binding = _binding(attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY")
        with mock.patch.object(
            r8u.os.path,
            "ismount",
            side_effect=lambda value: Path(value) == binding.scheduler_root,
        ):
            path = attempt / Path(
                r8u._r8u_scheduler_log_relative_path(binding).as_posix()
            )
            _write_mode(path, b"x", 0o600)
            _expect_code(
                lambda: r8u._r8u_scheduler_log_evidence(
                    path=path,
                    binding=binding,
                    approved_device=attempt.stat().st_dev,
                ),
                "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID",
            )

    with _attempt() as attempt:
        binding = _binding(attempt, role="FRESH_R8U_R2_BATCH16_RECOVERY")
        alias = binding.scheduler_root
        alias.rmdir()
        target = attempt / "private-scheduler-target"
        _private_directory(target)
        alias.symlink_to(target, target_is_directory=True)
        path = target / r8u._r8u_scheduler_log_basename(binding)
        _write_mode(path, b"x", 0o600)
        lexical_path = alias / path.name
        _expect_code(
            lambda: r8u._r8u_scheduler_log_evidence(
                path=lexical_path,
                binding=binding,
                approved_device=attempt.stat().st_dev,
            ),
            "R8U_SCHEDULER_LOG_TOPOLOGY_INVALID",
        )

def test_all_four_log_roles_share_one_closed_descriptor_validator() -> None:
    cases = (
        ("FRESH_R8U_R2_BATCH16_RECOVERY", "NONE", "8123456"),
        ("R8U_R2_CONTINUATION_ARRAY_TASK", "17", "8123457"),
        ("R8U_R2_CONTINUATION_ARRAY_TASK", "18", "8123457"),
        ("R8U_R2_CONTINUATION_ARRAY_TASK", "19", "8123457"),
        ("R8U_R2_COHORT_FINALIZER", "NONE", "8123458"),
    )
    with _attempt() as attempt:
        observed: set[str] = set()
        for role, task_id, job_id in cases:
            binding = _binding(
                attempt, role=role, task_id=task_id, job_id=job_id
            )
            relative = r8u._r8u_scheduler_log_relative_path(binding)
            assert relative.as_posix() not in observed
            observed.add(relative.as_posix())
            evidence = _read_evidence(attempt, binding)
            assert evidence["role"] == role
            assert evidence["job_id"] == job_id
            assert evidence["task_id"] == task_id
        assert len(observed) == 5

        invalid = _binding(
            attempt,
            role="R8U_R2_CONTINUATION_ARRAY_TASK",
            task_id="16",
            job_id="8123457",
        )
        _expect_code(
            lambda: r8u._r8u_scheduler_log_basename(invalid),
            "R8U_SCHEDULER_LOG_JOB_BINDING_INVALID",
        )


def test_scheduler_logs_do_not_change_scientific_projection() -> None:
    payload = b"sealed failed scheduler evidence\n"
    with _attempt() as attempt, _patched_failed_payload(payload), ExitStack() as stack:
        _private_directory(attempt / "protected")
        _write_mode(attempt / "protected" / "sealed", b"science", 0o600)
        fixed = frozenset({PurePosixPath("."), PurePosixPath("protected")})
        excluded = frozenset({PurePosixPath("r8u_batch16_recovery")})
        for patcher in (
            mock.patch.object(
                r8u, "R8U_FIXED_REQUIRED_DIRECTORY_PATHS", fixed
            ),
            mock.patch.object(
                r8u, "R8U_PROTECTED_DIRECTORY_ROLE_ROOTS", fixed
            ),
            mock.patch.object(
                r8u, "R8U_SUCCESSOR_EXCLUSION_PATHS", excluded
            ),
        ):
            stack.enter_context(patcher)
        baseline = r8u._r8u_scan_attempt_content()
        binding = _binding(attempt, role="FAILED_R8U_BATCH16_RECOVERY")
        _read_evidence(
            attempt, binding, payload=payload, mode=0o644
        )
        observed = r8u._r8u_scan_attempt_content()
        assert observed.authority == baseline.authority
        assert (
            observed.regular_file_path_set_sha256
            == baseline.regular_file_path_set_sha256
        )
        assert len(observed.scheduler_evidence) == 1
        assert observed.scheduler_evidence[0]["role"] == (
            "FAILED_R8U_BATCH16_RECOVERY"
        )


def test_failed_epoch_root_is_read_only_and_fresh_epoch_is_distinct() -> None:
    assert r8u.R8U_FAILED_RECOVERY_ROOT.name == "r8u_batch16_recovery"
    assert r8u.R8U_RECOVERY_ROOT.name == "r8u_r2_batch16_recovery"
    assert r8u.R8U_FAILED_RECOVERY_ROOT != r8u.R8U_RECOVERY_ROOT
    source = inspect.getsource(r8u)
    write_markers = (
        "_write_private_json(R8U_FAILED_RECOVERY_ROOT",
        "_create_private_directory_no_clobber(R8U_FAILED_RECOVERY_ROOT",
        "os.chmod(R8U_FAILED_RECOVERY_ROOT",
        "shutil.rmtree(R8U_FAILED_RECOVERY_ROOT",
        "os.rename(R8U_FAILED_RECOVERY_ROOT",
    )
    assert all(marker not in source for marker in write_markers)
    submit_source = inspect.getsource(r8u.submit_r8u_batch16_recovery)
    assert "_r8u_validate_pre_mutation_projections()" not in submit_source
    assert submit_source.count("_validate_r8u_initial_recovery_qstat(") == 1
    assert "while " not in inspect.getsource(
        r8u._validate_r8u_initial_recovery_qstat
    )
    assert '"new_qsub_submissions": 1' in submit_source
    assert "submit_r8u_continuation_17_19" not in submit_source
    for worker in (
        r8u.run_r8u_continuation_array_task,
        r8u.run_r8u_continuation_finalizer,
    ):
        worker_source = inspect.getsource(worker)
        assert worker_source.index(
            "_validate_r8u_continuation_chain("
        ) < worker_source.index("_r8u_validate_attempt_content_authority()")


def test_zero_byte_failed_evidence_rejects_visible_path_replacement() -> None:
    empty_sha256 = hashlib.sha256(b"").hexdigest()
    with _attempt() as attempt:
        path = attempt / "recovery.qsub.stderr.restricted"
        _write_mode(path, b"", 0o600)
        original_read = os.read

        def replace_visible_path(descriptor: int, size: int) -> bytes:
            payload = original_read(descriptor, size)
            replacement = attempt / "replacement.restricted"
            _write_mode(replacement, b"", 0o600)
            os.replace(replacement, path)
            return payload

        with mock.patch.object(
            r8u.os, "read", side_effect=replace_visible_path
        ):
            _expect_code(
                lambda: r8u._read_private_exact(
                    path, size=0, digest=empty_sha256
                ),
                "R8R_EXACT_CONTROL_FILE_INVALID",
            )


def test_failed_epoch_inventory_is_exact_and_any_drift_is_blocking() -> None:
    with _attempt() as attempt:
        root = attempt / "r8u_batch16_recovery"
        scheduler = root / "scheduler"
        _private_directory(root)
        _private_directory(scheduler)
        submission = (
            json.dumps(
                {
                    "implementation_commit": (
                        r8u.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
                    ),
                    "recovery_job_name": r8u.R8U_FAILED_RECOVERY_JOB_NAME,
                    "recovery_job_id": r8u.R8U_FAILED_RECOVERY_JOB_ID,
                    "scheduler_submission_count": 1,
                    "cloud_requests": 0,
                    "download_reruns": 0,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        payloads = {
            "failed_partial_seal.restricted.json": b"partial\n",
            "recovery_authority.restricted.json": b"authority\n",
            "recovery_capacity.restricted.json": b"capacity\n",
            (
                "scheduler/"
                f"{r8u.R8U_FAILED_RECOVERY_JOB_NAME}.o"
                f"{r8u.R8U_FAILED_RECOVERY_JOB_ID}"
            ): b"terminal scheduler log\n",
            "scheduler/recovery.qsub.exit_status.restricted": b"0\n",
            "scheduler/recovery.qsub.stderr.restricted": b"",
            "scheduler/recovery.qsub.stdout.restricted": (
                f"{r8u.R8U_FAILED_RECOVERY_JOB_ID}\n".encode()
            ),
            "scheduler/submission_receipt.restricted.json": submission,
        }
        authorities = {
            relative: (len(payload), hashlib.sha256(payload).hexdigest())
            for relative, payload in payloads.items()
        }
        for relative, payload in payloads.items():
            mode = 0o644 if ".o7352656" in relative else 0o600
            _write_mode(root / Path(relative), payload, mode)
        with mock.patch.object(
            r8u, "R8U_FAILED_RECOVERY_FILE_AUTHORITIES", authorities
        ):
            authority = r8u._r8u_failed_recovery_epoch_authority()
            assert authority["job_id"] == r8u.R8U_FAILED_RECOVERY_JOB_ID
            assert authority["namespace_file_count"] == 8
            assert authority["scheduler_evidence"]["mode"] == "0644"
            assert authority["qacct_failed"] == 0
            assert authority["qacct_exit_status"] == 78

            control = root / "recovery_capacity.restricted.json"
            control.write_bytes(b"drifted\n")
            control.chmod(0o600)
            _expect_code(
                r8u._r8u_failed_recovery_epoch_authority,
                "R8U_FAILED_RECOVERY_EVIDENCE_INVALID",
            )

        control.write_bytes(payloads["recovery_capacity.restricted.json"])
        control.chmod(0o600)
        _write_mode(root / "unexpected.restricted", b"x", 0o600)
        with mock.patch.object(
            r8u, "R8U_FAILED_RECOVERY_FILE_AUTHORITIES", authorities
        ):
            _expect_code(
                r8u._r8u_failed_recovery_epoch_authority,
                "R8U_FAILED_RECOVERY_EVIDENCE_INVALID",
            )


def test_five_epoch_authority_is_exact_and_models_remain_unreachable() -> None:
    current = "a" * 40
    assert r8u._r8u_implementation_authority_epochs(current) == {
        "scientific_commit": r8u.ORIGINAL_SCIENTIFIC_COMMIT,
        "r8r_implementation_commit": r8u.R8U_STARTING_IMPLEMENTATION_COMMIT,
        "r8u_base_implementation_commit": r8u.R8U_BASE_IMPLEMENTATION_COMMIT,
        "r8u_projection_repair_commit": (
            r8u.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
        ),
        "r8u_scheduler_log_repair_commit": current,
    }
    source = inspect.getsource(r8u.submit_r8u_batch16_recovery)
    for forbidden in (
        "fit(",
        "predict(",
        "confirmatory",
        "submit_r8u_continuation_17_19(",
    ):
        assert forbidden not in source
