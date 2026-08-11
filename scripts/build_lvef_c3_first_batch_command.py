#!/usr/bin/env python3
"""Build the exact, owner-private, unexecuted first-batch dispatcher command.

The command is an authority artifact, not an authorization receipt.  It cannot
create a receipt, submit a scheduler job, or contact a cloud service.  Its
deterministic bytes are bound into the production authority packet before the
launch envelope is created.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Sequence


ATTEMPT_RE = re.compile(r"^lvef_c3_phase1ee_[a-z0-9][a-z0-9_-]{5,63}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PROJECTNB_PREFIX = Path("/restricted/projectnb/mimicecho")
WORKTREE_PREFIX = Path(
    "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
)


class FirstBatchCommandError(ValueError):
    """Fail-closed command-authority error."""


def _require_no_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    if not absolute.is_absolute():
        raise FirstBatchCommandError("COMMAND_PATH_NOT_ABSOLUTE")
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
        cursor /= part
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise FirstBatchCommandError("COMMAND_PATH_ANCESTOR_MISSING") from exc
        if stat.S_ISLNK(metadata.st_mode):
            if sys.platform == "darwin" and cursor == Path("/var"):
                continue
            raise FirstBatchCommandError("COMMAND_PATH_SYMLINK_ANCESTOR")
        if not stat.S_ISDIR(metadata.st_mode):
            raise FirstBatchCommandError("COMMAND_PATH_ANCESTOR_NOT_DIRECTORY")


def _under(path: Path, root: Path, code: str) -> None:
    if not path.is_absolute():
        raise FirstBatchCommandError(f"{code}_NOT_ABSOLUTE")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FirstBatchCommandError(f"{code}_OUTSIDE_APPROVED_ROOT") from exc


def render(
    *,
    governing_commit: str,
    attempt_id: str,
    dispatcher: Path,
    execution_environment: Path,
    launch_authority: Path,
    dispatch_authorization: Path,
    body_authorization: Path,
) -> bytes:
    """Return the sole accepted first-batch command bytes."""
    if not COMMIT_RE.fullmatch(governing_commit) or not ATTEMPT_RE.fullmatch(attempt_id):
        raise FirstBatchCommandError("COMMAND_IDENTITY_INVALID")
    _under(dispatcher, WORKTREE_PREFIX, "DISPATCHER")
    for path, code in (
        (execution_environment, "EXECUTION_ENVIRONMENT"),
        (launch_authority, "LAUNCH_AUTHORITY"),
        (dispatch_authorization, "DISPATCH_AUTHORIZATION"),
        (body_authorization, "BODY_AUTHORIZATION"),
    ):
        _under(path, PROJECTNB_PREFIX, code)
    values = (
        str(dispatcher),
        str(execution_environment),
        str(launch_authority),
        str(dispatch_authorization),
        str(body_authorization),
    )
    if any("'" in value or "\n" in value or "\r" in value for value in values):
        raise FirstBatchCommandError("COMMAND_PATH_UNSAFE")
    text = f"""#!/usr/bin/env bash
# UNEXECUTED — owner authorization receipts do not exist in Phase 1E-F.
# Governing commit: {governing_commit}
# Production attempt: {attempt_id}
set -euo pipefail
umask 077

DISPATCHER='{dispatcher}'
EXECUTION_ENV='{execution_environment}'
LAUNCH_AUTHORITY='{launch_authority}'
DISPATCH_AUTHORIZATION='{dispatch_authorization}'
BODY_AUTHORIZATION='{body_authorization}'

# These owner-private receipts must be created only in a later, explicitly
# authorized phase.  This file grants no scope and is never executed here.
[[ -f "$DISPATCH_AUTHORIZATION" && ! -L "$DISPATCH_AUTHORIZATION" ]]
[[ -f "$BODY_AUTHORIZATION" && ! -L "$BODY_AUTHORIZATION" ]]
"$DISPATCHER" --submit FIRST_BATCH_DOWNLOAD \
  "$EXECUTION_ENV" "$LAUNCH_AUTHORITY" "$DISPATCH_AUTHORIZATION" - 1
"""
    return text.encode("utf-8")


def validate_exact(path: Path, *, expected: bytes) -> None:
    _require_no_symlink_ancestors(path)
    if path.is_symlink() or not path.is_file():
        raise FirstBatchCommandError("COMMAND_NOT_REGULAR")
    metadata = path.stat(follow_symlinks=False)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise FirstBatchCommandError("COMMAND_NOT_OWNER_PRIVATE")
    observed = path.read_bytes()
    if observed != expected:
        raise FirstBatchCommandError("COMMAND_BYTES_NOT_EXACT")
    completed = subprocess.run(
        ["bash", "-n", str(path)], capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise FirstBatchCommandError("COMMAND_BASH_SYNTAX_INVALID")


def write_no_clobber(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise FirstBatchCommandError("COMMAND_OUTPUT_COLLISION")
    _require_no_symlink_ancestors(path)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise FirstBatchCommandError("COMMAND_OUTPUT_PARENT_INVALID")
    parent = path.parent.stat(follow_symlinks=False)
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
        raise FirstBatchCommandError("COMMAND_OUTPUT_PARENT_NOT_PRIVATE")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--dispatcher", type=Path, required=True)
    parser.add_argument("--execution-environment", type=Path, required=True)
    parser.add_argument("--launch-authority", type=Path, required=True)
    parser.add_argument("--dispatch-authorization", type=Path, required=True)
    parser.add_argument("--body-authorization", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = render(
            governing_commit=args.governing_commit,
            attempt_id=args.attempt_id,
            dispatcher=args.dispatcher,
            execution_environment=args.execution_environment,
            launch_authority=args.launch_authority,
            dispatch_authorization=args.dispatch_authorization,
            body_authorization=args.body_authorization,
        )
        write_no_clobber(args.output, payload)
        validate_exact(args.output, expected=payload)
    except (FirstBatchCommandError, OSError):
        print("FIRST_BATCH_COMMAND=FAILED")
        return 78
    print("FIRST_BATCH_COMMAND=CREATED_UNEXECUTED")
    print("AUTHORIZATION_SCOPES_GRANTED=0")
    print("SCHEDULER_SUBMISSIONS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
