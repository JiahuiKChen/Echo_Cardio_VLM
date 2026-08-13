from __future__ import annotations

import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_canary_state as canary_state
import lvef_c3_execution_state as execution_state


COMMIT = "a" * 40
RUN_ID = "lvef_c3_exact_five_canary_ab12cd34"


def _sandbox() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory()
    parent = Path(temporary.name).resolve(strict=True) / "private"
    parent.mkdir(mode=0o700)
    return temporary, parent / "lifecycle"


def _initialize(root: Path) -> dict[str, object]:
    return canary_state.initialize_state(
        root=root,
        governing_commit=COMMIT,
        run_id=RUN_ID,
    )


def _symlinked_authority_root(
    temporary_root: Path,
) -> tuple[Path, Path]:
    temporary_root = temporary_root.resolve(strict=True)
    real_authority = temporary_root / "real-owner-private"
    real_authority.mkdir(mode=0o700)
    real_canary = real_authority / "exact_five_canary"
    real_canary.mkdir(mode=0o700)
    alias = temporary_root / "owner_private"
    alias.symlink_to(real_authority, target_is_directory=True)
    return (
        alias / "exact_five_canary" / "lifecycle_state",
        real_canary / "lifecycle_state",
    )


def _transition(
    root: Path, expected: str, target: str, reason: str = "TEST_TRANSITION"
) -> dict[str, object]:
    return canary_state.transition_state(
        root=root,
        expected_current=expected,
        target_state=target,
        governing_commit=COMMIT,
        run_id=RUN_ID,
        reason_code=reason,
    )


def _assert_error(code: str, callable_value) -> None:
    try:
        callable_value()
    except canary_state.CanaryStateError as exc:
        assert exc.code == code
        assert str(exc) == code
    else:
        raise AssertionError(f"expected {code}")


def test_initialize_load_and_forward_only_lifecycle() -> None:
    temporary, root = _sandbox()
    try:
        initial = _initialize(root)
        state_path = root / "canary_state.restricted.json"
        assert initial["current_state"] == "PRECANARY_READY"
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
        assert canary_state.load_state(
            root=root,
            expected_governing_commit=COMMIT,
            expected_run_id=RUN_ID,
        ) == initial

        prepared = _transition(
            root,
            "PRECANARY_READY",
            "CANARY_AUTHORITY_PREPARED",
            "AUTHORITY_VALIDATED",
        )
        sealed = _transition(
            root,
            "CANARY_AUTHORITY_PREPARED",
            "CANARY_MANIFEST_SEALED",
            "MANIFEST_VALIDATED",
        )
        executing = _transition(
            root,
            "CANARY_MANIFEST_SEALED",
            "CANARY_EXECUTING",
            "DISPATCH_COMMITTED",
        )
        terminal = _transition(
            root,
            "CANARY_EXECUTING",
            "CANARY_TERMINAL_PASS",
            "AGGREGATE_RECEIPT_PASS",
        )
        assert [
            prepared["current_state"],
            sealed["current_state"],
            executing["current_state"],
            terminal["current_state"],
        ] == [
            "CANARY_AUTHORITY_PREPARED",
            "CANARY_MANIFEST_SEALED",
            "CANARY_EXECUTING",
            "CANARY_TERMINAL_PASS",
        ]
        assert terminal["transition_count"] == 4
        tracked = execution_state.load_execution_state()
        assert not canary_state.permits_execute(
            tracked,
            terminal,
            governing_commit=COMMIT,
            run_id=RUN_ID,
        )
    finally:
        temporary.cleanup()


def test_two_edge_preparation_transaction_records_both_edges_once() -> None:
    temporary, root = _sandbox()
    try:
        _initialize(root)
        state = canary_state.transition_sequence(
            root=root,
            expected_current="PRECANARY_READY",
            targets=("CANARY_AUTHORITY_PREPARED", "CANARY_MANIFEST_SEALED"),
            governing_commit=COMMIT,
            run_id=RUN_ID,
            reason_codes=("AUTHORITY_VALIDATED", "MANIFEST_VALIDATED"),
            bindings=(
                {"authorization_sha256": "1" * 64},
                {"manifest_sha256": "2" * 64},
            ),
        )
        assert state["current_state"] == "CANARY_MANIFEST_SEALED"
        assert state["transition_count"] == 2
        assert [row["target_state"] for row in state["history"]] == [
            "CANARY_AUTHORITY_PREPARED",
            "CANARY_MANIFEST_SEALED",
        ]
        tracked = execution_state.load_execution_state()
        canary_state.assert_execute_permitted(
            tracked,
            state,
            governing_commit=COMMIT,
            run_id=RUN_ID,
        )
    finally:
        temporary.cleanup()


def test_failed_transition_preserves_prior_bytes_exactly() -> None:
    temporary, root = _sandbox()
    try:
        _initialize(root)
        state_path = root / "canary_state.restricted.json"
        before = state_path.read_bytes()
        _assert_error(
            "CANARY_STATE_TRANSITION_NOT_PERMITTED",
            lambda: canary_state.transition_sequence(
                root=root,
                expected_current="PRECANARY_READY",
                targets=("CANARY_AUTHORITY_PREPARED", "CANARY_EXECUTING"),
                governing_commit=COMMIT,
                run_id=RUN_ID,
                reason_codes=("AUTHORITY_VALIDATED", "INVALID_SKIP"),
            ),
        )
        assert state_path.read_bytes() == before
        assert not (root / "canary_state.restricted.json.partial").exists()
        assert not (root / "canary_state.restricted.json.lock").exists()

        _assert_error(
            "CANARY_STATE_EXPECTED_CURRENT_MISMATCH",
            lambda: _transition(
                root,
                "CANARY_AUTHORITY_PREPARED",
                "CANARY_MANIFEST_SEALED",
            ),
        )
        assert state_path.read_bytes() == before
    finally:
        temporary.cleanup()


def test_replace_failure_and_lock_collision_preserve_prior_bytes() -> None:
    temporary, root = _sandbox()
    try:
        _initialize(root)
        state_path = root / "canary_state.restricted.json"
        before = state_path.read_bytes()
        with mock.patch.object(
            canary_state.os, "replace", side_effect=OSError("synthetic")
        ):
            _assert_error(
                "CANARY_STATE_ATOMIC_REPLACE_FAILED",
                lambda: _transition(
                    root,
                    "PRECANARY_READY",
                    "CANARY_AUTHORITY_PREPARED",
                ),
            )
        assert state_path.read_bytes() == before

        lock = root / "canary_state.restricted.json.lock"
        lock.write_bytes(b"locked\n")
        os.chmod(lock, 0o600)
        _assert_error(
            "CANARY_STATE_MUTATION_LOCKED",
            lambda: _transition(
                root,
                "PRECANARY_READY",
                "CANARY_AUTHORITY_PREPARED",
            ),
        )
        assert state_path.read_bytes() == before
    finally:
        temporary.cleanup()


def test_initialize_publish_failure_leaves_no_partial_snapshot() -> None:
    temporary, root = _sandbox()
    try:
        with mock.patch.object(
            canary_state.os, "link", side_effect=OSError("synthetic")
        ):
            _assert_error(
                "CANARY_STATE_ATOMIC_PUBLISH_FAILED",
                lambda: _initialize(root),
            )
        assert not (root / "canary_state.restricted.json").exists()
        assert not (root / "canary_state.restricted.json.partial").exists()
        assert not (root / "canary_state.restricted.json.lock").exists()
    finally:
        temporary.cleanup()


def test_initialize_rejects_private_ancestor_symlink_before_mutation() -> None:
    temporary = tempfile.TemporaryDirectory()
    try:
        alias_root, real_root = _symlinked_authority_root(
            Path(temporary.name)
        )
        _assert_error(
            "CANARY_STATE_ANCESTOR_SYMLINK_FORBIDDEN",
            lambda: _initialize(alias_root),
        )
        assert not real_root.exists()
        assert not list(real_root.parent.glob("*.lock"))
        assert not list(real_root.parent.glob("*.partial"))
    finally:
        temporary.cleanup()


def test_load_and_transition_reject_private_ancestor_symlink() -> None:
    temporary = tempfile.TemporaryDirectory()
    try:
        alias_root, real_root = _symlinked_authority_root(
            Path(temporary.name)
        )
        _initialize(real_root)
        state_path = real_root / "canary_state.restricted.json"
        before = state_path.read_bytes()
        _assert_error(
            "CANARY_STATE_ANCESTOR_SYMLINK_FORBIDDEN",
            lambda: canary_state.load_state(root=alias_root),
        )
        _assert_error(
            "CANARY_STATE_ANCESTOR_SYMLINK_FORBIDDEN",
            lambda: _transition(
                alias_root,
                "PRECANARY_READY",
                "CANARY_AUTHORITY_PREPARED",
            ),
        )
        assert state_path.read_bytes() == before
        assert not (real_root / "canary_state.restricted.json.lock").exists()
        assert not (real_root / "canary_state.restricted.json.partial").exists()
    finally:
        temporary.cleanup()


def test_private_leaf_io_rejects_symlink_ancestor() -> None:
    temporary = tempfile.TemporaryDirectory()
    try:
        alias_root, real_root = _symlinked_authority_root(
            Path(temporary.name)
        )
        real_root.mkdir(mode=0o700)
        real_snapshot = real_root / "canary_state.restricted.json"
        real_snapshot.write_bytes(b"{}\n")
        real_snapshot.chmod(0o600)
        _assert_error(
            "CANARY_STATE_ANCESTOR_SYMLINK_FORBIDDEN",
            lambda: canary_state._read_private_snapshot(
                alias_root / "canary_state.restricted.json"
            ),
        )
        _assert_error(
            "CANARY_STATE_ANCESTOR_SYMLINK_FORBIDDEN",
            lambda: canary_state._write_new_file_no_clobber(
                alias_root / "probe.restricted.json", b"{}\n"
            ),
        )
        _assert_error(
            "CANARY_STATE_ANCESTOR_SYMLINK_FORBIDDEN",
            lambda: canary_state._acquire_lock(
                alias_root / "probe.restricted.json.lock"
            ),
        )
        assert real_snapshot.read_bytes() == b"{}\n"
        assert not (real_root / "probe.restricted.json").exists()
        assert not (real_root / "probe.restricted.json.lock").exists()
    finally:
        temporary.cleanup()


def test_resolved_synthetic_root_topology_is_accepted() -> None:
    temporary, root = _sandbox()
    try:
        resolved_root = root.resolve(strict=False)
        initial = _initialize(resolved_root)
        assert canary_state.load_state(root=resolved_root) == initial
    finally:
        temporary.cleanup()


def test_identity_tampering_terminal_rules_and_no_tracked_mutation() -> None:
    temporary, root = _sandbox()
    try:
        tracked_before = execution_state.DEFAULT_STATE_PATH.read_bytes()
        initial = _initialize(root)
        _assert_error(
            "CANARY_STATE_GOVERNING_COMMIT_MISMATCH",
            lambda: canary_state.load_state(
                root=root, expected_governing_commit="b" * 40
            ),
        )
        _assert_error(
            "CANARY_STATE_RUN_ID_MISMATCH",
            lambda: canary_state.load_state(
                root=root,
                expected_run_id="lvef_c3_exact_five_canary_ffffffff",
            ),
        )
        _assert_error(
            "CANARY_STATE_TRANSITION_NOT_PERMITTED",
            lambda: _transition(
                root, "PRECANARY_READY", "CANARY_TERMINAL_FAIL"
            ),
        )
        tracked = execution_state.load_execution_state()
        _assert_error(
            "CANARY_STATE_EXECUTION_NOT_PERMITTED",
            lambda: canary_state.assert_execute_permitted(
                tracked,
                initial,
                governing_commit=COMMIT,
                run_id=RUN_ID,
            ),
        )
        assert execution_state.DEFAULT_STATE_PATH.read_bytes() == tracked_before
    finally:
        temporary.cleanup()


def test_snapshot_closed_schema_and_tracked_hash_binding() -> None:
    temporary, root = _sandbox()
    try:
        value = _initialize(root)
        tracked = execution_state.load_execution_state()
        unknown = copy.deepcopy(value)
        unknown["unknown"] = True
        _assert_error(
            "CANARY_STATE_SNAPSHOT_SCHEMA_INVALID",
            lambda: canary_state.validate_state_value(unknown, tracked),
        )
        changed = copy.deepcopy(value)
        changed["tracked_execution_state_sha256"] = "0" * 64
        _assert_error(
            "CANARY_STATE_SNAPSHOT_INVARIANT_INVALID",
            lambda: canary_state.validate_state_value(changed, tracked),
        )
        schema = json.loads(
            (ROOT / "configs/lvef_c3_canary_state_snapshot_schema_v1.json").read_text()
        )
        assert schema["additionalProperties"] is False
        assert schema["properties"]["governing_commit"] == {
            "$ref": "#/$defs/commit"
        }
    finally:
        temporary.cleanup()


def test_cli_initialize_show_and_transition_are_aggregate_safe() -> None:
    temporary, root = _sandbox()
    try:
        output = io.StringIO()
        with redirect_stdout(output):
            assert canary_state.main(
                [
                    "--initialize",
                    "--state-root",
                    str(root),
                    "--governing-commit",
                    COMMIT,
                    "--run-id",
                    RUN_ID,
                ]
            ) == 0
        emitted = output.getvalue()
        assert "CURRENT_STATE=PRECANARY_READY" in emitted
        assert COMMIT not in emitted
        assert RUN_ID not in emitted
        assert str(root) not in emitted

        output = io.StringIO()
        with redirect_stdout(output):
            assert canary_state.main(
                [
                    "--transition",
                    "--state-root",
                    str(root),
                    "--governing-commit",
                    COMMIT,
                    "--run-id",
                    RUN_ID,
                    "--expected-current",
                    "PRECANARY_READY",
                    "--target-state",
                    "CANARY_AUTHORITY_PREPARED",
                    "--reason-code",
                    "AUTHORITY_VALIDATED",
                    "--binding",
                    f"0:authorization_sha256={'1' * 64}",
                ]
            ) == 0
        assert "CURRENT_STATE=CANARY_AUTHORITY_PREPARED" in output.getvalue()
    finally:
        temporary.cleanup()
