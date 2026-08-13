#!/usr/bin/env python3
"""Owner-private lifecycle state for one exact-five LVEF C3 canary.

The tracked execution-state authority defines the lifecycle and private path
policy.  This module mutates only one owner-private snapshot.  Mutations are
explicit expected-current transitions protected by a sibling O_EXCL lock and
an fsynced temporary file followed by atomic replacement.  The tracked state
file is read-only and is never modified here.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Final, Mapping, MutableMapping, Sequence


SCRIPT_ROOT: Final = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPT_ROOT))

import lvef_c3_execution_state as execution_state


DEFAULT_EXECUTION_STATE: Final = execution_state.DEFAULT_STATE_PATH
DEFAULT_PRIVATE_STATE_ROOT: Final = Path(execution_state.CANARY_PRIVATE_STATE_ROOT)
SCHEMA_VERSION: Final = 1
ARTIFACT_TYPE: Final = "lvef_c3_canary_lifecycle_state_v1"
RUN_ID_RE: Final = re.compile(r"^lvef_c3_exact_five_canary_[a-z0-9]{8}$")
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
REASON_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,95}$")
BINDING_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

SNAPSHOT_KEYS: Final = frozenset(
    {
        "schema_version",
        "artifact_type",
        "governing_commit",
        "run_id",
        "tracked_execution_state_sha256",
        "current_state",
        "transition_count",
        "previous_snapshot_sha256",
        "created_at_utc",
        "updated_at_utc",
        "history",
    }
)
TRANSITION_KEYS: Final = frozenset(
    {
        "sequence",
        "expected_current",
        "target_state",
        "reason_code",
        "bindings",
        "transitioned_at_utc",
    }
)
EXECUTE_STATES: Final = frozenset(
    {"CANARY_MANIFEST_SEALED", "CANARY_EXECUTING"}
)


class CanaryStateError(RuntimeError):
    """A fixed aggregate-safe state failure."""

    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "CANARY_STATE_INVALID"
        super().__init__(code)
        self.code = code


def canonical_snapshot_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CanaryStateError("CANARY_STATE_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CanaryStateError("CANARY_STATE_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise CanaryStateError("CANARY_STATE_TIMESTAMP_INVALID")
    return parsed


def _validate_identity(governing_commit: Any, run_id: Any) -> tuple[str, str]:
    if not isinstance(governing_commit, str) or COMMIT_RE.fullmatch(governing_commit) is None:
        raise CanaryStateError("CANARY_STATE_GOVERNING_COMMIT_INVALID")
    if not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None:
        raise CanaryStateError("CANARY_STATE_RUN_ID_INVALID")
    return governing_commit, run_id


def _validate_bindings(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise CanaryStateError("CANARY_STATE_BINDINGS_INVALID")
    normalized: dict[str, str] = {}
    for name, digest in value.items():
        if (
            not isinstance(name, str)
            or BINDING_NAME_RE.fullmatch(name) is None
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
        ):
            raise CanaryStateError("CANARY_STATE_BINDINGS_INVALID")
        normalized[name] = digest
    return dict(sorted(normalized.items()))


def _load_tracked_authority(
    path: Path,
) -> execution_state.ExecutionState:
    path = Path(path)
    try:
        before = path.read_bytes()
        state = execution_state.load_execution_state(path)
        after = path.read_bytes()
    except execution_state.ExecutionStateError as exc:
        raise CanaryStateError("CANARY_STATE_TRACKED_AUTHORITY_INVALID") from exc
    except OSError as exc:
        raise CanaryStateError("CANARY_STATE_TRACKED_AUTHORITY_UNREADABLE") from exc
    if before != after:
        raise CanaryStateError("CANARY_STATE_TRACKED_AUTHORITY_CHANGED")
    if state.canary_lifecycle.tracked_state_mutation_permitted is not False:
        raise CanaryStateError("CANARY_STATE_TRACKED_MUTATION_POLICY_INVALID")
    return state


def _state_paths(
    root: Path, tracked: execution_state.ExecutionState
) -> tuple[Path, Path, Path]:
    root = Path(root)
    if not root.is_absolute():
        raise CanaryStateError("CANARY_STATE_ROOT_NOT_ABSOLUTE")
    lifecycle = tracked.canary_lifecycle
    return (
        root / lifecycle.state_filename,
        root / lifecycle.lock_filename,
        root / lifecycle.temporary_filename,
    )


def _resolve_private_root(root: Path) -> Path:
    """Resolve system topology after rejecting symlinks in the private chain.

    Synthetic roots on macOS can arrive below system aliases such as ``/var``.
    Walking from the private leaf outward only while directories remain
    current-owner/private avoids treating those system aliases as part of the
    owner-private authority subtree.  Any symlink reached before that boundary
    is an authority-subtree ancestor and is rejected before a mutation.
    """

    requested = Path(root)
    if not requested.is_absolute() or requested == Path(requested.anchor):
        raise CanaryStateError("CANARY_STATE_ROOT_INVALID")
    lexical = Path(os.path.abspath(os.fspath(requested)))
    current = lexical if os.path.lexists(lexical) else lexical.parent
    while current != Path(current.anchor):
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise CanaryStateError("CANARY_STATE_ANCESTOR_INVALID") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise CanaryStateError(
                "CANARY_STATE_ANCESTOR_SYMLINK_FORBIDDEN"
            )
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) not in {0o700, 0o2700}
        ):
            break
        current = current.parent
    try:
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise CanaryStateError("CANARY_STATE_ROOT_INVALID") from exc
    if not resolved.is_absolute() or resolved == Path(resolved.anchor):
        raise CanaryStateError("CANARY_STATE_ROOT_INVALID")
    return resolved


def _require_resolved_ancestor_directories(path: Path) -> None:
    """Reject an ancestor redirect on a previously resolved private path."""

    path = Path(path)
    if not path.is_absolute():
        raise CanaryStateError("CANARY_STATE_PATH_INVALID")
    current = Path(path.anchor)
    for component in path.parts[1:-1]:
        current /= component
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise CanaryStateError("CANARY_STATE_ANCESTOR_INVALID") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise CanaryStateError(
                "CANARY_STATE_ANCESTOR_SYMLINK_FORBIDDEN"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            raise CanaryStateError("CANARY_STATE_ANCESTOR_INVALID")


def _validate_private_root(root: Path, *, create: bool) -> Path:
    root = _resolve_private_root(root)
    _require_resolved_ancestor_directories(root)
    if not os.path.lexists(root):
        if not create:
            raise CanaryStateError("CANARY_STATE_ROOT_MISSING")
        parent = root.parent
        try:
            parent_metadata = os.lstat(parent)
        except OSError as exc:
            raise CanaryStateError("CANARY_STATE_PARENT_INVALID") from exc
        if (
            stat.S_ISLNK(parent_metadata.st_mode)
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(parent_metadata.st_mode) not in {0o700, 0o2700}
        ):
            raise CanaryStateError("CANARY_STATE_PARENT_INVALID")
        try:
            os.mkdir(root, mode=0o700)
        except OSError as exc:
            raise CanaryStateError("CANARY_STATE_ROOT_CREATE_FAILED") from exc
    try:
        metadata = os.lstat(root)
    except OSError as exc:
        raise CanaryStateError("CANARY_STATE_ROOT_INVALID") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise CanaryStateError("CANARY_STATE_ROOT_INVALID")
    return root


def _read_private_snapshot(path: Path) -> tuple[bytes, MutableMapping[str, Any]]:
    _require_resolved_ancestor_directories(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CanaryStateError("CANARY_STATE_SNAPSHOT_UNREADABLE") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise CanaryStateError("CANARY_STATE_SNAPSHOT_NOT_PRIVATE")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read()
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryStateError("CANARY_STATE_SNAPSHOT_JSON_INVALID") from exc
    if not isinstance(value, dict) or payload != canonical_snapshot_bytes(value):
        raise CanaryStateError("CANARY_STATE_SNAPSHOT_NOT_CANONICAL")
    return payload, value


def validate_state_value(
    value: Any,
    tracked_state: execution_state.ExecutionState,
    *,
    expected_governing_commit: str | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Validate one closed lifecycle snapshot against tracked policy."""

    if not isinstance(value, Mapping) or set(value) != SNAPSHOT_KEYS:
        raise CanaryStateError("CANARY_STATE_SNAPSHOT_SCHEMA_INVALID")
    governing_commit, run_id = _validate_identity(
        value.get("governing_commit"), value.get("run_id")
    )
    if expected_governing_commit is not None and governing_commit != expected_governing_commit:
        raise CanaryStateError("CANARY_STATE_GOVERNING_COMMIT_MISMATCH")
    if expected_run_id is not None and run_id != expected_run_id:
        raise CanaryStateError("CANARY_STATE_RUN_ID_MISMATCH")
    lifecycle = tracked_state.canary_lifecycle
    all_states = frozenset(
        (*lifecycle.ordered_nonterminal_states, *lifecycle.terminal_states)
    )
    history = value.get("history")
    previous_sha = value.get("previous_snapshot_sha256")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != ARTIFACT_TYPE
        or value.get("tracked_execution_state_sha256")
        != tracked_state.canonical_authority_sha256
        or value.get("current_state") not in all_states
        or isinstance(value.get("transition_count"), bool)
        or not isinstance(value.get("transition_count"), int)
        or not isinstance(history, list)
        or value["transition_count"] != len(history)
        or len(history) > 4
        or (not history and previous_sha is not None)
        or (
            history
            and (
                not isinstance(previous_sha, str)
                or SHA256_RE.fullmatch(previous_sha) is None
            )
        )
    ):
        raise CanaryStateError("CANARY_STATE_SNAPSHOT_INVARIANT_INVALID")
    created = _validate_timestamp(value.get("created_at_utc"))
    updated = _validate_timestamp(value.get("updated_at_utc"))
    current = lifecycle.initial_state
    prior_transition_time = created
    for index, item in enumerate(history, start=1):
        if not isinstance(item, Mapping) or set(item) != TRANSITION_KEYS:
            raise CanaryStateError("CANARY_STATE_HISTORY_SCHEMA_INVALID")
        expected = item.get("expected_current")
        target = item.get("target_state")
        if (
            item.get("sequence") != index
            or expected != current
            or (expected, target) not in lifecycle.transitions
            or not isinstance(item.get("reason_code"), str)
            or REASON_RE.fullmatch(item["reason_code"]) is None
        ):
            raise CanaryStateError("CANARY_STATE_HISTORY_TRANSITION_INVALID")
        _validate_bindings(item.get("bindings"))
        transitioned = _validate_timestamp(item.get("transitioned_at_utc"))
        if transitioned < prior_transition_time or transitioned > updated:
            raise CanaryStateError("CANARY_STATE_HISTORY_TIMESTAMP_INVALID")
        prior_transition_time = transitioned
        current = str(target)
    if (
        value.get("current_state") != current
        or (not history and updated != created)
        or (history and updated != prior_transition_time)
    ):
        raise CanaryStateError("CANARY_STATE_CURRENT_STATE_INVALID")
    return copy.deepcopy(dict(value))


def _acquire_lock(lock_path: Path) -> int:
    _require_resolved_ancestor_directories(lock_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except FileExistsError as exc:
        raise CanaryStateError("CANARY_STATE_MUTATION_LOCKED") from exc
    except OSError as exc:
        raise CanaryStateError("CANARY_STATE_LOCK_CREATE_FAILED") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        os.close(descriptor)
        try:
            os.unlink(lock_path)
        except OSError:
            pass
        raise CanaryStateError("CANARY_STATE_LOCK_INVALID")
    return descriptor


def _release_lock(lock_path: Path, descriptor: int) -> None:
    try:
        try:
            os.close(descriptor)
        except OSError:
            # The mutation outcome is already authoritative; a close error
            # must not turn a successful atomic publish into a reported
            # failure that falsely promises prior-byte preservation.
            pass
    finally:
        try:
            _require_resolved_ancestor_directories(lock_path)
            os.unlink(lock_path)
        except (CanaryStateError, OSError):
            pass


def _write_new_file_no_clobber(path: Path, payload: bytes) -> None:
    _require_resolved_ancestor_directories(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise CanaryStateError("CANARY_STATE_OUTPUT_COLLISION") from exc
    except OSError as exc:
        raise CanaryStateError("CANARY_STATE_OUTPUT_CREATE_FAILED") from exc
    succeeded = False
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written < 1:
                raise CanaryStateError("CANARY_STATE_OUTPUT_WRITE_FAILED")
            offset += written
        getattr(os, "fdatasync", os.fsync)(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(payload)
        ):
            raise CanaryStateError("CANARY_STATE_OUTPUT_POSTWRITE_INVALID")
        succeeded = True
    finally:
        os.close(descriptor)
        if not succeeded:
            try:
                _require_resolved_ancestor_directories(path)
                os.unlink(path)
            except (CanaryStateError, OSError):
                pass


def initialize_state(
    *,
    root: Path = DEFAULT_PRIVATE_STATE_ROOT,
    execution_state_path: Path = DEFAULT_EXECUTION_STATE,
    governing_commit: str,
    run_id: str,
) -> dict[str, Any]:
    """Create the initial PRECANARY_READY private snapshot exactly once."""

    governing_commit, run_id = _validate_identity(governing_commit, run_id)
    tracked = _load_tracked_authority(execution_state_path)
    if not tracked.permits("prepare_exact_five_canary_authority"):
        raise CanaryStateError("CANARY_STATE_INITIALIZATION_SCOPE_DENIED")
    root = _validate_private_root(root, create=True)
    state_path, lock_path, temporary_path = _state_paths(root, tracked)
    lock_descriptor = _acquire_lock(lock_path)
    try:
        if os.path.lexists(state_path) or os.path.lexists(temporary_path):
            raise CanaryStateError("CANARY_STATE_OUTPUT_COLLISION")
        now = _utc_now()
        value = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": ARTIFACT_TYPE,
            "governing_commit": governing_commit,
            "run_id": run_id,
            "tracked_execution_state_sha256": tracked.canonical_authority_sha256,
            "current_state": tracked.canary_lifecycle.initial_state,
            "transition_count": 0,
            "previous_snapshot_sha256": None,
            "created_at_utc": now,
            "updated_at_utc": now,
            "history": [],
        }
        validate_state_value(
            value,
            tracked,
            expected_governing_commit=governing_commit,
            expected_run_id=run_id,
        )
        _write_new_file_no_clobber(
            temporary_path, canonical_snapshot_bytes(value)
        )
        try:
            _require_resolved_ancestor_directories(temporary_path)
            _require_resolved_ancestor_directories(state_path)
            os.link(temporary_path, state_path, follow_symlinks=False)
        except FileExistsError as exc:
            raise CanaryStateError("CANARY_STATE_OUTPUT_COLLISION") from exc
        except OSError as exc:
            raise CanaryStateError("CANARY_STATE_ATOMIC_PUBLISH_FAILED") from exc
        _require_resolved_ancestor_directories(temporary_path)
        os.unlink(temporary_path)
        return copy.deepcopy(value)
    finally:
        try:
            _require_resolved_ancestor_directories(temporary_path)
            if os.path.lexists(temporary_path) and not temporary_path.is_symlink():
                os.unlink(temporary_path)
        except (CanaryStateError, OSError):
            pass
        _release_lock(lock_path, lock_descriptor)


def load_state(
    *,
    root: Path = DEFAULT_PRIVATE_STATE_ROOT,
    execution_state_path: Path = DEFAULT_EXECUTION_STATE,
    expected_governing_commit: str | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Load and validate the current owner-private lifecycle snapshot."""

    tracked = _load_tracked_authority(execution_state_path)
    root = _validate_private_root(root, create=False)
    state_path, _, _ = _state_paths(root, tracked)
    _, value = _read_private_snapshot(state_path)
    return validate_state_value(
        value,
        tracked,
        expected_governing_commit=expected_governing_commit,
        expected_run_id=expected_run_id,
    )


def transition_sequence(
    *,
    root: Path = DEFAULT_PRIVATE_STATE_ROOT,
    execution_state_path: Path = DEFAULT_EXECUTION_STATE,
    expected_current: str,
    targets: Sequence[str],
    governing_commit: str,
    run_id: str,
    reason_codes: Sequence[str],
    bindings: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Atomically apply one or more explicit forward transitions.

    All edges are recorded in history but are published as one state-file
    replacement.  Any failure before replacement leaves the prior state bytes
    unchanged.  This is used to commit authority preparation and manifest
    sealing as one transaction after every artifact has already validated.
    """

    governing_commit, run_id = _validate_identity(governing_commit, run_id)
    if (
        not isinstance(expected_current, str)
        or isinstance(targets, (str, bytes))
        or not isinstance(targets, Sequence)
        or not targets
        or isinstance(reason_codes, (str, bytes))
        or not isinstance(reason_codes, Sequence)
        or len(reason_codes) != len(targets)
    ):
        raise CanaryStateError("CANARY_STATE_TRANSITION_ARGUMENTS_INVALID")
    binding_values: Sequence[Mapping[str, str]] = (
        [{} for _ in targets] if bindings is None else bindings
    )
    if (
        isinstance(binding_values, (str, bytes))
        or not isinstance(binding_values, Sequence)
        or len(binding_values) != len(targets)
    ):
        raise CanaryStateError("CANARY_STATE_TRANSITION_ARGUMENTS_INVALID")
    normalized_bindings = [_validate_bindings(item) for item in binding_values]
    if any(
        not isinstance(reason, str) or REASON_RE.fullmatch(reason) is None
        for reason in reason_codes
    ):
        raise CanaryStateError("CANARY_STATE_REASON_CODE_INVALID")

    tracked = _load_tracked_authority(execution_state_path)
    root = _validate_private_root(root, create=False)
    state_path, lock_path, temporary_path = _state_paths(root, tracked)
    lock_descriptor = _acquire_lock(lock_path)
    try:
        if os.path.lexists(temporary_path):
            raise CanaryStateError("CANARY_STATE_TEMPORARY_COLLISION")
        prior_payload, prior = _read_private_snapshot(state_path)
        prior = validate_state_value(
            prior,
            tracked,
            expected_governing_commit=governing_commit,
            expected_run_id=run_id,
        )
        if prior["current_state"] != expected_current:
            raise CanaryStateError("CANARY_STATE_EXPECTED_CURRENT_MISMATCH")
        current = expected_current
        updated = copy.deepcopy(prior)
        now = _utc_now()
        for target, reason, edge_bindings in zip(
            targets, reason_codes, normalized_bindings, strict=True
        ):
            if not isinstance(target, str) or (
                current,
                target,
            ) not in tracked.canary_lifecycle.transitions:
                raise CanaryStateError("CANARY_STATE_TRANSITION_NOT_PERMITTED")
            updated["history"].append(
                {
                    "sequence": len(updated["history"]) + 1,
                    "expected_current": current,
                    "target_state": target,
                    "reason_code": reason,
                    "bindings": edge_bindings,
                    "transitioned_at_utc": now,
                }
            )
            current = target
        updated["current_state"] = current
        updated["transition_count"] = len(updated["history"])
        updated["previous_snapshot_sha256"] = _sha256(prior_payload)
        updated["updated_at_utc"] = now
        validate_state_value(
            updated,
            tracked,
            expected_governing_commit=governing_commit,
            expected_run_id=run_id,
        )
        updated_payload = canonical_snapshot_bytes(updated)
        _write_new_file_no_clobber(temporary_path, updated_payload)
        try:
            _require_resolved_ancestor_directories(temporary_path)
            _require_resolved_ancestor_directories(state_path)
            os.replace(temporary_path, state_path)
        except OSError as exc:
            raise CanaryStateError("CANARY_STATE_ATOMIC_REPLACE_FAILED") from exc
        return copy.deepcopy(updated)
    finally:
        try:
            _require_resolved_ancestor_directories(temporary_path)
            if os.path.lexists(temporary_path) and not temporary_path.is_symlink():
                os.unlink(temporary_path)
        except (CanaryStateError, OSError):
            pass
        _release_lock(lock_path, lock_descriptor)


def transition_state(
    *,
    root: Path = DEFAULT_PRIVATE_STATE_ROOT,
    execution_state_path: Path = DEFAULT_EXECUTION_STATE,
    expected_current: str,
    target_state: str,
    governing_commit: str,
    run_id: str,
    reason_code: str,
    bindings: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Apply exactly one expected-current forward transition."""

    return transition_sequence(
        root=root,
        execution_state_path=execution_state_path,
        expected_current=expected_current,
        targets=(target_state,),
        governing_commit=governing_commit,
        run_id=run_id,
        reason_codes=(reason_code,),
        bindings=({},) if bindings is None else (bindings,),
    )


def assert_execute_permitted(
    tracked_state: execution_state.ExecutionState,
    private_state: Mapping[str, Any],
    *,
    governing_commit: str,
    run_id: str,
) -> None:
    """Require the sealed/executing private state without globally enabling execute."""

    normalized = validate_state_value(
        private_state,
        tracked_state,
        expected_governing_commit=governing_commit,
        expected_run_id=run_id,
    )
    if (
        not tracked_state.permits("begin_exact_five_canary_execution")
        or normalized["current_state"] not in EXECUTE_STATES
    ):
        raise CanaryStateError("CANARY_STATE_EXECUTION_NOT_PERMITTED")


def permits_execute(
    tracked_state: execution_state.ExecutionState,
    private_state: Mapping[str, Any],
    *,
    governing_commit: str,
    run_id: str,
) -> bool:
    try:
        assert_execute_permitted(
            tracked_state,
            private_state,
            governing_commit=governing_commit,
            run_id=run_id,
        )
    except CanaryStateError:
        return False
    return True


def _parse_binding_arguments(
    raw: Sequence[str], count: int
) -> tuple[dict[str, str], ...]:
    values = [dict() for _ in range(count)]
    for item in raw:
        try:
            prefix, assignment = item.split(":", 1)
            name, digest = assignment.split("=", 1)
            index = int(prefix)
        except (ValueError, TypeError) as exc:
            raise CanaryStateError("CANARY_STATE_CLI_BINDING_INVALID") from exc
        if index < 0 or index >= count:
            raise CanaryStateError("CANARY_STATE_CLI_BINDING_INVALID")
        values[index][name] = digest
    return tuple(values)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--initialize", action="store_true")
    modes.add_argument("--show", action="store_true")
    modes.add_argument("--transition", action="store_true")
    modes.add_argument("--transition-sequence", action="store_true")
    parser.add_argument("--state-root", type=Path, default=DEFAULT_PRIVATE_STATE_ROOT)
    parser.add_argument("--execution-state", type=Path, default=DEFAULT_EXECUTION_STATE)
    parser.add_argument("--governing-commit")
    parser.add_argument("--run-id")
    parser.add_argument("--expected-current")
    parser.add_argument("--target-state", action="append", default=[])
    parser.add_argument("--reason-code", action="append", default=[])
    parser.add_argument(
        "--binding",
        action="append",
        default=[],
        metavar="INDEX:NAME=SHA256",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.initialize:
            if args.governing_commit is None or args.run_id is None:
                raise CanaryStateError("CANARY_STATE_CLI_IDENTITY_REQUIRED")
            result = initialize_state(
                root=args.state_root,
                execution_state_path=args.execution_state,
                governing_commit=args.governing_commit,
                run_id=args.run_id,
            )
        elif args.show:
            result = load_state(
                root=args.state_root,
                execution_state_path=args.execution_state,
                expected_governing_commit=args.governing_commit,
                expected_run_id=args.run_id,
            )
        else:
            if (
                args.governing_commit is None
                or args.run_id is None
                or args.expected_current is None
                or not args.target_state
                or len(args.reason_code) != len(args.target_state)
                or (args.transition and len(args.target_state) != 1)
            ):
                raise CanaryStateError("CANARY_STATE_CLI_TRANSITION_REQUIRED")
            result = transition_sequence(
                root=args.state_root,
                execution_state_path=args.execution_state,
                expected_current=args.expected_current,
                targets=tuple(args.target_state),
                governing_commit=args.governing_commit,
                run_id=args.run_id,
                reason_codes=tuple(args.reason_code),
                bindings=_parse_binding_arguments(
                    tuple(args.binding), len(args.target_state)
                ),
            )
    except (CanaryStateError, execution_state.ExecutionStateError) as exc:
        code = getattr(exc, "code", "CANARY_STATE_BLOCKED")
        print(f"LVEF_C3_CANARY_STATE=BLOCKED_{code}")
        return 78
    print("LVEF_C3_CANARY_STATE=PASS")
    print(f"CURRENT_STATE={result['current_state']}")
    print(f"TRANSITION_COUNT={result['transition_count']}")
    print("IDENTIFIERS_EMITTED=NO")
    print("PRIVATE_PATHS_EMITTED=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CanaryStateError",
    "DEFAULT_PRIVATE_STATE_ROOT",
    "assert_execute_permitted",
    "canonical_snapshot_bytes",
    "initialize_state",
    "load_state",
    "permits_execute",
    "transition_sequence",
    "transition_state",
    "validate_state_value",
]
