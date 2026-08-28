from __future__ import annotations

"""Synthetic contracts for the R8U stable attempt-content projection.

These tests inspect metadata only.  They never contact SCC, read a DICOM/NPZ
body, invoke a scheduler, or exercise a scientific stage.
"""

from contextlib import ExitStack, contextmanager
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


def _private_file(path: Path, payload: bytes) -> None:
    _private_directory(path.parent)
    path.write_bytes(payload)
    path.chmod(0o600)


@contextmanager
def _synthetic_authority() -> Iterator[tuple[Path, dict[str, Any]]]:
    with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
        attempt = Path(temporary)
        attempt.chmod(0o700)
        _private_directory(attempt / "protected" / "empty-required")
        _private_directory(attempt / "optional-empty")
        _private_file(attempt / "protected" / "sealed.bin", b"sealed-body")
        fixed = frozenset(
            {
                PurePosixPath("."),
                PurePosixPath("protected"),
                PurePosixPath("protected/empty-required"),
            }
        )
        protected = frozenset({PurePosixPath("protected")})
        excluded = frozenset(
            {
                PurePosixPath("successor"),
                PurePosixPath("cohort_finalization"),
            }
        )
        for patcher in (
            mock.patch.object(r8u, "ATTEMPT_ROOT", attempt),
            mock.patch.object(r8u, "R8U_FIXED_REQUIRED_DIRECTORY_PATHS", fixed),
            mock.patch.object(r8u, "R8U_PROTECTED_DIRECTORY_ROLE_ROOTS", protected),
            mock.patch.object(r8u, "R8U_SUCCESSOR_EXCLUSION_PATHS", excluded),
            mock.patch.object(r8u, "_r8u_nested_mount_paths", return_value=frozenset()),
            mock.patch.object(r8u.os.path, "ismount", return_value=False),
        ):
            stack.enter_context(patcher)
        baseline = r8u._r8u_scan_attempt_content()
        historical = dict(r8u.R8U_HISTORICAL_ATTEMPT_TREE_PROJECTION)
        historical["regular_file_count"] = baseline.authority[
            "regular_file_count"
        ]
        historical["regular_file_bytes"] = baseline.authority[
            "regular_file_bytes"
        ]
        for patcher in (
            mock.patch.object(
                r8u, "R8U_HISTORICAL_ATTEMPT_TREE_PROJECTION", historical
            ),
            mock.patch.object(
                r8u,
                "R8U_BASELINE_REGULAR_FILE_PATH_SET_SHA256",
                baseline.regular_file_path_set_sha256,
            ),
            mock.patch.object(
                r8u,
                "R8U_BASELINE_REGULAR_FILE_PROJECTION_SHA256",
                baseline.authority["regular_file_projection_sha256"],
            ),
            mock.patch.object(
                r8u,
                "R8U_BASELINE_REQUIRED_DIRECTORY_COUNT",
                baseline.authority["required_directory_count"],
            ),
            mock.patch.object(
                r8u,
                "R8U_BASELINE_REQUIRED_DIRECTORY_PROJECTION_SHA256",
                baseline.authority["required_directory_projection_sha256"],
            ),
        ):
            stack.enter_context(patcher)
        yield attempt, dict(baseline.authority)


def test_historical_root_inclusive_projection_is_preserved_truthfully() -> None:
    assert r8u.R8U_HISTORICAL_ATTEMPT_TREE_PROJECTION == {
        "regular_file_count": 1_190_913,
        "directory_count_including_attempt_root": 473,
        "regular_file_bytes": 1_081_833_737_569,
        "symlink_count": 0,
        "nonregular_count": 0,
        "metadata_projection_sha256": (
            "32bf9ad2317b544bcfbfbd47f553d3c4f6476dad5e11fae88e8c8b6d6d28a429"
        ),
    }
    assert (
        r8u.R8U_DIRECTORY_COMPATIBILITY_ROLE
        == "ATTEMPT_ROOT_PROJECTION_ROW_OMITTED"
    )
    assert (
        r8u.R8U_DIRECTORY_DIFFERENCE_CLASS
        == "BENIGN_OPTIONAL_EMPTY_DIRECTORY_LIFECYCLE"
    )
    assert "OPTIONAL" not in r8u.R8U_DIRECTORY_COMPATIBILITY_ROLE
    assert r8u.R8U_BASELINE_REGULAR_FILE_PATH_SET_SHA256 == (
        "36d41e28ee46ae2c70735965bafc3af429cc6d62e9aa5a9b67db07b8dff4dfe4"
    )
    assert r8u.R8U_BASELINE_REGULAR_FILE_PROJECTION_SHA256 == (
        "11ec20f9b5d81024affddfa521695a779389ee296f1f2899630a6a1dfaaf5536"
    )
    assert r8u.R8U_BASELINE_REQUIRED_DIRECTORY_COUNT == 471
    assert r8u.R8U_BASELINE_REQUIRED_DIRECTORY_PROJECTION_SHA256 == (
        "cb9308386a77950eaba7ab4e17c9f3f82a9fcd4b9ec99590452501c74a2f92a1"
    )


def test_closed_projection_and_optional_empty_add_remove_are_stable() -> None:
    with _synthetic_authority() as (attempt, baseline):
        observed = r8u._r8u_validate_attempt_content_authority()
        assert set(observed) == r8u.R8U_ATTEMPT_CONTENT_AUTHORITY_KEYS
        assert observed == baseline

        (attempt / "optional-empty").rmdir()
        without_optional = r8u._r8u_validate_attempt_content_authority()
        assert without_optional["optional_empty_directory_count"] == (
            baseline["optional_empty_directory_count"] - 1
        )

        _private_directory(attempt / "another-optional")
        _private_directory(attempt / "another-optional" / "nested-empty")
        with_added_optional = r8u._r8u_validate_attempt_content_authority()
        assert with_added_optional["optional_empty_directory_count"] == (
            without_optional["optional_empty_directory_count"] + 2
        )
        for key in r8u.R8U_ATTEMPT_CONTENT_AUTHORITY_KEYS.difference(
            {"optional_empty_directory_count"}
        ):
            assert with_added_optional[key] == baseline[key]


def test_required_directory_removal_and_topology_changes_fail_by_role() -> None:
    with _synthetic_authority() as (attempt, _baseline):
        (attempt / "protected" / "empty-required").rmdir()
        _expect_code(
            r8u._r8u_validate_attempt_content_authority,
            "R8U_REQUIRED_DIRECTORY_MISSING",
        )

    with _synthetic_authority() as (attempt, _baseline):
        required = attempt / "protected" / "empty-required"
        required.chmod(0o755)
        _expect_code(
            r8u._r8u_validate_attempt_content_authority,
            "R8U_REQUIRED_DIRECTORY_TOPOLOGY_INVALID",
        )

    with _synthetic_authority() as (attempt, _baseline):
        (attempt / "optional-empty").chmod(0o755)
        _expect_code(
            r8u._r8u_validate_attempt_content_authority,
            "R8U_OPTIONAL_EMPTY_DIRECTORY_COMPATIBILITY_INVALID",
        )


def test_same_size_replacement_fails_metadata_projection() -> None:
    with _synthetic_authority() as (attempt, _baseline):
        sealed = attempt / "protected" / "sealed.bin"
        original_size = sealed.stat().st_size
        sealed.unlink()
        _private_file(sealed, b"x" * original_size)
        _expect_code(
            r8u._r8u_validate_attempt_content_authority,
            "R8U_REGULAR_FILE_PROJECTION_CHANGED",
        )


def test_regular_file_path_set_add_remove_and_rename_fail() -> None:
    for mutation in ("rename", "add", "remove"):
        with _synthetic_authority() as (attempt, _baseline):
            sealed = attempt / "protected" / "sealed.bin"
            if mutation == "rename":
                sealed.rename(sealed.with_name("renamed.bin"))
            elif mutation == "add":
                _private_file(attempt / "protected" / "added.bin", b"added")
            else:
                sealed.unlink()
            _expect_code(
                r8u._r8u_validate_attempt_content_authority,
                "R8U_REGULAR_FILE_PATH_SET_CHANGED",
            )


def test_symlink_fifo_socket_and_device_entries_fail() -> None:
    for kind in ("symlink", "fifo", "socket", "device"):
        with _synthetic_authority() as (attempt, _baseline):
            unsafe = attempt / f"unsafe-{kind}"
            if kind == "symlink":
                unsafe.symlink_to(attempt / "protected" / "sealed.bin")
            elif kind == "fifo":
                os.mkfifo(unsafe, 0o600)
            else:
                _private_file(unsafe, b"placeholder")
                real_lstat = r8u._r8u_stable_lstat

                def fake_special(path: Path) -> Any:
                    value = real_lstat(path)
                    if path != unsafe:
                        return value
                    return SimpleNamespace(
                        st_mode=(
                            stat.S_IFSOCK if kind == "socket" else stat.S_IFCHR
                        )
                        | 0o600,
                        st_uid=value.st_uid,
                        st_gid=value.st_gid,
                        st_dev=value.st_dev,
                        st_ino=value.st_ino,
                        st_nlink=value.st_nlink,
                        st_size=value.st_size,
                        st_mtime_ns=value.st_mtime_ns,
                        st_ctime_ns=value.st_ctime_ns,
                    )

            patcher = (
                mock.patch.object(
                    r8u, "_r8u_stable_lstat", side_effect=fake_special
                )
                if kind in {"socket", "device"}
                else mock.patch.object(
                    r8u,
                    "_r8u_stable_lstat",
                    wraps=r8u._r8u_stable_lstat,
                )
            )
            with patcher:
                _expect_code(
                    r8u._r8u_validate_attempt_content_authority,
                    "R8U_UNSAFE_NONREGULAR_ENTRY",
                )


def test_cross_device_path_escape_fails_closed() -> None:
    with _synthetic_authority() as (attempt, _baseline):
        sealed = attempt / "protected" / "sealed.bin"
        real_lstat = r8u._r8u_stable_lstat

        def cross_device(path: Path) -> Any:
            value = real_lstat(path)
            if path != sealed:
                return value
            return SimpleNamespace(
                st_mode=value.st_mode,
                st_uid=value.st_uid,
                st_gid=value.st_gid,
                st_dev=value.st_dev + 1,
                st_ino=value.st_ino,
                st_nlink=value.st_nlink,
                st_size=value.st_size,
                st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns,
            )

        with mock.patch.object(
            r8u, "_r8u_stable_lstat", side_effect=cross_device
        ):
            _expect_code(
                r8u._r8u_validate_attempt_content_authority,
                "R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID",
            )


def test_lexical_path_escape_and_duplicate_directory_entry_fail_closed() -> None:
    class _SyntheticScandir:
        def __init__(self, entries: list[Any]):
            self.entries = entries

        def __enter__(self) -> Iterator[Any]:
            return iter(self.entries)

        def __exit__(self, *_args: Any) -> None:
            return None

    class _EscapeEntry:
        name = "escape"
        path = "/outside-the-attempt/escape"

    for mutation in ("escape", "duplicate"):
        with _synthetic_authority() as (attempt, _baseline):
            real_scandir = os.scandir

            def injected(path: Path) -> Any:
                iterator = real_scandir(path)
                if Path(path) != attempt:
                    return iterator
                try:
                    entries = list(iterator)
                finally:
                    iterator.close()
                if mutation == "escape":
                    entries.append(_EscapeEntry())
                else:
                    entries.append(entries[0])
                return _SyntheticScandir(entries)

            with mock.patch.object(r8u.os, "scandir", side_effect=injected):
                _expect_code(
                    r8u._r8u_validate_attempt_content_authority,
                    "R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID",
                )


def test_successor_exclusions_are_exact_and_pristine_before_first_write() -> None:
    assert PurePosixPath("raw/c3_batch_015") not in (
        r8u.R8U_SUCCESSOR_EXCLUSION_PATHS
    )
    assert PurePosixPath(
        "extracted_cache/c3_batch_015/dicom_extraction.partial"
    ) not in r8u.R8U_SUCCESSOR_EXCLUSION_PATHS
    for expected in (
        "r8u_batch16_recovery",
        "r8u_continuation_17_19",
        "extracted_cache/c3_batch_015/dicom_extraction",
        "batches/c3_batch_015/echoprime",
        "batches/c3_batch_015/preservation",
        "batches/c3_batch_015/extraction_resume_ledger.restricted.json",
        "batches/c3_batch_015/pooling_resume_ledger.restricted.json",
        "batches/c3_batch_015/cache_retirement_eligible_resume_ledger.restricted.json",
        "batches/c3_batch_015/final_resume_ledger.restricted.json",
        "cache_retirement_authorizations/c3_batch_015.authorization.json",
    ):
        assert PurePosixPath(expected) in r8u.R8U_SUCCESSOR_EXCLUSION_PATHS

    with _synthetic_authority() as (attempt, _baseline):
        r8u._r8u_validate_pristine_successor_exclusions()
        _private_directory(attempt / "cohort_finalization")
        r8u._r8u_validate_pristine_successor_exclusions()
        _private_file(attempt / "cohort_finalization" / "unexpected", b"x")
        _expect_code(
            r8u._r8u_validate_pristine_successor_exclusions,
            "R8U_SUCCESSOR_EXCLUSION_NOT_PRISTINE",
        )

    with _synthetic_authority() as (attempt, _baseline):
        _private_directory(attempt / "successor")
        _expect_code(
            r8u._r8u_validate_pristine_successor_exclusions,
            "R8U_SUCCESSOR_EXCLUSION_NOT_PRISTINE",
        )


def test_successor_exclusions_cannot_hide_unsafe_topology() -> None:
    with _synthetic_authority() as (attempt, baseline):
        successor = attempt / "successor"
        _private_directory(successor)
        # Dynamic successor regular files remain outside the immutable-prefix
        # digest; their scientific meaning is enforced by stage receipts.
        _private_file(successor / "structurally-safe.bin", b"successor")
        assert r8u._r8u_validate_attempt_content_authority() == baseline

        (successor / "unsafe-link").symlink_to(
            attempt / "protected" / "sealed.bin"
        )
        _expect_code(
            r8u._r8u_validate_attempt_content_authority,
            "R8U_UNSAFE_NONREGULAR_ENTRY",
        )

    with _synthetic_authority() as (attempt, _baseline):
        successor = attempt / "successor"
        _private_directory(successor)
        os.mkfifo(successor / "unsafe-fifo", 0o600)
        _expect_code(
            r8u._r8u_validate_attempt_content_authority,
            "R8U_UNSAFE_NONREGULAR_ENTRY",
        )

    with _synthetic_authority() as (attempt, _baseline):
        successor = attempt / "successor"
        _private_directory(successor)
        successor.chmod(0o755)
        _expect_code(
            r8u._r8u_validate_attempt_content_authority,
            "R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID",
        )

    with _synthetic_authority() as (attempt, _baseline):
        successor = attempt / "successor"
        _private_directory(successor)
        with mock.patch.object(
            r8u,
            "_r8u_nested_mount_paths",
            return_value=frozenset({successor}),
        ):
            _expect_code(
                r8u._r8u_validate_attempt_content_authority,
                "R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID",
            )

    with _synthetic_authority() as (attempt, _baseline):
        successor = attempt / "successor"
        _private_directory(successor)
        artifact = successor / "cross-device.bin"
        _private_file(artifact, b"successor")
        real_lstat = r8u._r8u_stable_lstat

        def cross_device(path: Path) -> Any:
            value = real_lstat(path)
            if path != artifact:
                return value
            return SimpleNamespace(
                st_mode=value.st_mode,
                st_uid=value.st_uid,
                st_gid=value.st_gid,
                st_dev=value.st_dev + 1,
                st_ino=value.st_ino,
                st_nlink=value.st_nlink,
                st_size=value.st_size,
                st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns,
            )

        with mock.patch.object(
            r8u, "_r8u_stable_lstat", side_effect=cross_device
        ):
            _expect_code(
                r8u._r8u_validate_attempt_content_authority,
                "R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID",
            )

    class _SyntheticScandir:
        def __init__(self, entries: list[Any]):
            self.entries = entries

        def __enter__(self) -> Iterator[Any]:
            return iter(self.entries)

        def __exit__(self, *_args: Any) -> None:
            return None

    class _EscapeEntry:
        name = "escape"
        path = "/outside-the-attempt/excluded-escape"

    with _synthetic_authority() as (attempt, _baseline):
        successor = attempt / "successor"
        _private_directory(successor)
        real_scandir = os.scandir

        def injected(path: Path) -> Any:
            iterator = real_scandir(path)
            if Path(path) != successor:
                return iterator
            try:
                entries = list(iterator)
            finally:
                iterator.close()
            entries.append(_EscapeEntry())
            return _SyntheticScandir(entries)

        with mock.patch.object(r8u.os, "scandir", side_effect=injected):
            _expect_code(
                r8u._r8u_validate_attempt_content_authority,
                "R8U_ATTEMPT_CONTENT_AUTHORITY_INVALID",
            )


def test_cohort_pristine_check_rejects_symlink_before_scandir() -> None:
    with _synthetic_authority() as (attempt, _baseline):
        cohort = attempt / "cohort_finalization"
        cohort.symlink_to(attempt / "protected", target_is_directory=True)
        with mock.patch.object(
            r8u.os,
            "scandir",
            side_effect=AssertionError("symlink target must not be scanned"),
        ):
            _expect_code(
                lambda: r8u._r8u_validate_pristine_exclusion_paths(
                    frozenset({PurePosixPath("cohort_finalization")})
                ),
                "R8U_SUCCESSOR_EXCLUSION_NOT_PRISTINE",
            )


def test_continuation_outputs_are_reproven_pristine_before_live_writes() -> None:
    import inspect

    for expected in (
        "r8u_continuation_17_19",
        "cohort_finalization",
        "raw/c3_batch_016",
        "raw/c3_batch_017",
        "raw/c3_batch_018",
        "batches/c3_batch_016",
        "batches/c3_batch_017",
        "batches/c3_batch_018",
        "extracted_cache/c3_batch_016",
        "extracted_cache/c3_batch_017",
        "extracted_cache/c3_batch_018",
    ):
        assert PurePosixPath(expected) in r8u.R8U_CONTINUATION_PRISTINE_PATHS
    assert PurePosixPath("r8u_continuation_17_19") not in (
        r8u.R8U_CONTINUATION_TASK_OUTPUT_PRISTINE_PATHS
    )

    source = inspect.getsource(r8u.submit_r8u_continuation_17_19)
    early_pristine = source.index(
        "_r8u_validate_pristine_continuation_exclusions()"
    )
    accounting = source.index("_query_recovery_accounting(")
    claim_write = source.index(
        "_write_private_json(R8U_CONTINUATION_CLAIM_PATH, claim)"
    )
    late_pristine = source.index(
        "_r8u_validate_pristine_continuation_task_outputs()"
    )
    first_qsub = source.index("scheduler._capture_qsub(")
    assert early_pristine < accounting < claim_write < late_pristine < first_qsub


def test_every_r8u_live_action_calls_portable_authority_before_and_after() -> None:
    import inspect

    submit_source = inspect.getsource(r8u.submit_r8u_batch16_recovery)
    assert "_r8u_validate_pre_mutation_projections()" in submit_source
    assert "_r8u_validate_attempt_content_authority()" in submit_source
    for action in (
        r8u.run_r8u_batch16_recovery,
        r8u.submit_r8u_continuation_17_19,
        r8u.run_r8u_continuation_array_task,
        r8u.run_r8u_continuation_finalizer,
    ):
        source = inspect.getsource(action)
        assert source.count("_r8u_validate_attempt_content_authority()") >= 2
