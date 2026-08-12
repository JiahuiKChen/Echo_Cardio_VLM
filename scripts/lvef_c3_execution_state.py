#!/usr/bin/env python3
"""Load the one closed Phase 1E-G execution-state authority.

The tracked ``.yaml`` file deliberately uses the JSON subset of YAML 1.2 so
the SCC control plane can reject duplicate keys and unknown fields using only
the Python standard library.  Preparation sequence identifiers are opaque;
their spelling is never parsed to infer an execution attempt.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Final, Iterable, MutableMapping, Sequence


DEFAULT_STATE_PATH: Final = (
    Path(__file__).resolve().parents[1] / "configs/lvef_c3_execution_state_v1.yaml"
)
SCHEMA_NAME: Final = "lvef_c3_execution_state_v1"
SCHEMA_VERSION: Final = 1
CURRENT_COMMIT_SENTINEL: Final = "GIT_HEAD"
COMMIT_RE: Final = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE: Final = re.compile(r"^[a-z0-9][a-z0-9_]*$")
FILENAME_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PERMITTED_SCOPES: Final = (
    "preflight_only",
    "capture_current_environment",
)
STATE_KEYS: Final = frozenset(
    {
        "schema_name",
        "schema_version",
        "branch",
        "historical_base_commit",
        "starting_authority_commit",
        "current_governing_commit",
        "execution_attempt_namespace",
        "logical_execution_attempt",
        "logical_execution_governing_commit",
        "preparation_sequence_id",
        "preparation_environment_filename",
        "preparation_environment_bytes",
        "next_unused_execution_attempt",
        "attempt_004_execution_count",
        "attempt_005_exists",
        "production_attempt_namespace",
        "prior_production_attempt",
        "next_unused_production_attempt",
        "production_attempt_006_exists",
        "permitted_execution_scopes",
    }
)


class ExecutionStateError(ValueError):
    """A fixed-code failure suitable for aggregate-safe command output."""

    def __init__(self, code: str):
        if re.fullmatch(r"[A-Z0-9_]+", code) is None:
            code = "EXECUTION_STATE_INVALID"
        super().__init__(code)
        self.code = code


def _strict_pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ExecutionStateError("EXECUTION_STATE_DUPLICATE_KEY")
        value[key] = item
    return value


def _attempt_id(namespace: str, number: int) -> str:
    return f"{namespace}_{number:03d}"


@dataclass(frozen=True)
class ExecutionState:
    schema_name: str
    schema_version: int
    branch: str
    historical_base_commit: str
    starting_authority_commit: str
    current_governing_commit: str
    execution_attempt_namespace: str
    logical_execution_attempt: int
    logical_execution_governing_commit: str
    preparation_sequence_id: str
    preparation_environment_filename: str
    preparation_environment_bytes: int
    next_unused_execution_attempt: int
    attempt_004_execution_count: int
    attempt_005_exists: bool
    production_attempt_namespace: str
    prior_production_attempt: int
    next_unused_production_attempt: int
    production_attempt_006_exists: bool
    permitted_execution_scopes: tuple[str, ...]

    @property
    def logical_execution_attempt_id(self) -> str:
        return _attempt_id(
            self.execution_attempt_namespace, self.logical_execution_attempt
        )

    @property
    def next_unused_execution_attempt_id(self) -> str:
        return _attempt_id(
            self.execution_attempt_namespace, self.next_unused_execution_attempt
        )

    @property
    def prior_production_attempt_id(self) -> str:
        return _attempt_id(
            self.production_attempt_namespace, self.prior_production_attempt
        )

    @property
    def next_unused_production_attempt_id(self) -> str:
        return _attempt_id(
            self.production_attempt_namespace, self.next_unused_production_attempt
        )

    @property
    def next_unused_execution_attempt_tag(self) -> str:
        return f"{self.next_unused_execution_attempt:03d}"

    def permits(self, scope: str) -> bool:
        return scope in self.permitted_execution_scopes


def _require_plain_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExecutionStateError(code)
    return value


def _require_commit(value: Any, code: str) -> str:
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        raise ExecutionStateError(code)
    return value


def validate_execution_state(value: Any) -> ExecutionState:
    if not isinstance(value, dict) or set(value) != STATE_KEYS:
        raise ExecutionStateError("EXECUTION_STATE_SCHEMA_NOT_CLOSED")
    if value.get("schema_name") != SCHEMA_NAME:
        raise ExecutionStateError("EXECUTION_STATE_SCHEMA_NAME_INVALID")
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ExecutionStateError("EXECUTION_STATE_SCHEMA_VERSION_INVALID")

    branch = value.get("branch")
    if branch != "codex/lvef-multitask-revalidation":
        raise ExecutionStateError("EXECUTION_STATE_BRANCH_INVALID")
    historical_base = _require_commit(
        value.get("historical_base_commit"), "EXECUTION_STATE_HISTORICAL_BASE_INVALID"
    )
    starting_commit = _require_commit(
        value.get("starting_authority_commit"), "EXECUTION_STATE_STARTING_COMMIT_INVALID"
    )
    if value.get("current_governing_commit") != CURRENT_COMMIT_SENTINEL:
        raise ExecutionStateError("EXECUTION_STATE_CURRENT_COMMIT_POLICY_INVALID")
    logical_commit = _require_commit(
        value.get("logical_execution_governing_commit"),
        "EXECUTION_STATE_LOGICAL_COMMIT_INVALID",
    )

    execution_namespace = value.get("execution_attempt_namespace")
    production_namespace = value.get("production_attempt_namespace")
    if (
        not isinstance(execution_namespace, str)
        or IDENTIFIER_RE.fullmatch(execution_namespace) is None
        or not isinstance(production_namespace, str)
        or IDENTIFIER_RE.fullmatch(production_namespace) is None
        or execution_namespace == production_namespace
    ):
        raise ExecutionStateError("EXECUTION_STATE_NAMESPACE_INVALID")

    logical_attempt = _require_plain_int(
        value.get("logical_execution_attempt"),
        "EXECUTION_STATE_LOGICAL_ATTEMPT_INVALID",
    )
    next_execution_attempt = _require_plain_int(
        value.get("next_unused_execution_attempt"),
        "EXECUTION_STATE_NEXT_ATTEMPT_INVALID",
    )
    attempt_execution_count = _require_plain_int(
        value.get("attempt_004_execution_count"),
        "EXECUTION_STATE_EXECUTION_COUNT_INVALID",
    )
    prior_production_attempt = _require_plain_int(
        value.get("prior_production_attempt"),
        "EXECUTION_STATE_PRIOR_PRODUCTION_ATTEMPT_INVALID",
    )
    next_production_attempt = _require_plain_int(
        value.get("next_unused_production_attempt"),
        "EXECUTION_STATE_NEXT_PRODUCTION_ATTEMPT_INVALID",
    )
    if (
        logical_attempt != 4
        or next_execution_attempt != logical_attempt + 1
        or attempt_execution_count != 1
        or value.get("attempt_005_exists") is not False
        or prior_production_attempt != 5
        or next_production_attempt != prior_production_attempt + 1
        or value.get("production_attempt_006_exists") is not False
    ):
        raise ExecutionStateError("EXECUTION_STATE_IDENTITY_INVARIANT_INVALID")

    preparation_sequence_id = value.get("preparation_sequence_id")
    preparation_filename = value.get("preparation_environment_filename")
    preparation_bytes = _require_plain_int(
        value.get("preparation_environment_bytes"),
        "EXECUTION_STATE_PREPARATION_SIZE_INVALID",
    )
    if (
        not isinstance(preparation_sequence_id, str)
        or IDENTIFIER_RE.fullmatch(preparation_sequence_id) is None
        or not isinstance(preparation_filename, str)
        or FILENAME_RE.fullmatch(preparation_filename) is None
        or "/" in preparation_filename
    ):
        raise ExecutionStateError("EXECUTION_STATE_PREPARATION_IDENTITY_INVALID")

    scopes = value.get("permitted_execution_scopes")
    if (
        not isinstance(scopes, list)
        or any(not isinstance(scope, str) for scope in scopes)
        or tuple(scopes) != PERMITTED_SCOPES
    ):
        raise ExecutionStateError("EXECUTION_STATE_SCOPE_INVALID")

    return ExecutionState(
        schema_name=SCHEMA_NAME,
        schema_version=SCHEMA_VERSION,
        branch=branch,
        historical_base_commit=historical_base,
        starting_authority_commit=starting_commit,
        current_governing_commit=CURRENT_COMMIT_SENTINEL,
        execution_attempt_namespace=execution_namespace,
        logical_execution_attempt=logical_attempt,
        logical_execution_governing_commit=logical_commit,
        preparation_sequence_id=preparation_sequence_id,
        preparation_environment_filename=preparation_filename,
        preparation_environment_bytes=preparation_bytes,
        next_unused_execution_attempt=next_execution_attempt,
        attempt_004_execution_count=attempt_execution_count,
        attempt_005_exists=False,
        production_attempt_namespace=production_namespace,
        prior_production_attempt=prior_production_attempt,
        next_unused_production_attempt=next_production_attempt,
        production_attempt_006_exists=False,
        permitted_execution_scopes=tuple(scopes),
    )


def load_execution_state(path: Path = DEFAULT_STATE_PATH) -> ExecutionState:
    try:
        if path.is_symlink() or not path.is_file():
            raise ExecutionStateError("EXECUTION_STATE_NOT_REGULAR")
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs
        )
    except ExecutionStateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutionStateError("EXECUTION_STATE_UNREADABLE") from exc
    return validate_execution_state(value)


EMIT_FIELDS: Final = {
    "branch": lambda state: state.branch,
    "historical_base_commit": lambda state: state.historical_base_commit,
    "starting_authority_commit": lambda state: state.starting_authority_commit,
    "current_governing_commit": lambda state: state.current_governing_commit,
    "logical_execution_attempt": lambda state: str(state.logical_execution_attempt),
    "logical_execution_attempt_id": lambda state: state.logical_execution_attempt_id,
    "logical_execution_governing_commit": lambda state: state.logical_execution_governing_commit,
    "preparation_sequence_id": lambda state: state.preparation_sequence_id,
    "preparation_environment_filename": lambda state: state.preparation_environment_filename,
    "next_unused_execution_attempt": lambda state: str(state.next_unused_execution_attempt),
    "next_unused_execution_attempt_id": lambda state: state.next_unused_execution_attempt_id,
    "next_unused_execution_attempt_tag": lambda state: state.next_unused_execution_attempt_tag,
    "prior_production_attempt_id": lambda state: state.prior_production_attempt_id,
    "next_unused_production_attempt_id": lambda state: state.next_unused_production_attempt_id,
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--field", choices=sorted(EMIT_FIELDS))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.field) == bool(args.check):
        parser.error("choose exactly one of --field or --check")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        state = load_execution_state(args.state)
    except ExecutionStateError as exc:
        print(f"LVEF_C3_EXECUTION_STATE=BLOCKED_{exc.code}")
        return 65
    if args.check:
        print("LVEF_C3_EXECUTION_STATE=PASS")
    else:
        print(EMIT_FIELDS[args.field](state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
