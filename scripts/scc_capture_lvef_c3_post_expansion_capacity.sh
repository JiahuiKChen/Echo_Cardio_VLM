#!/usr/bin/env bash
# Capture only the control-tier supplement to an immutable research receipt.
set -euo pipefail
umask 077

EXPECTED_BRANCH='codex/lvef-multitask-revalidation'
CAPTURE_ENV=''

safe_fail() {
  printf '%s\n' "PHASE1EE_POST_EXPANSION_CAPACITY=FAILED_$1"
  exit 2
}

usage() {
  printf '%s\n' \
    'usage: scc_capture_lvef_c3_post_expansion_capacity.sh --capture-env FILE' >&2
  exit 64
}

stat_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

is_private_directory() {
  case "$(stat_mode "$1")" in
    700|2700) return 0 ;;
    *) return 1 ;;
  esac
}

stat_follow_size() {
  stat -Lc '%s' "$1" 2>/dev/null || stat -Lf '%z' "$1"
}

stat_follow_device() {
  stat -Lc '%d' "$1" 2>/dev/null || stat -Lf '%d' "$1"
}

stat_follow_inode() {
  stat -Lc '%i' "$1" 2>/dev/null || stat -Lf '%i' "$1"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --capture-env)
      [[ $# -ge 2 ]] || usage
      CAPTURE_ENV="$2"
      shift 2
      ;;
    *) usage ;;
  esac
done
[[ -n "$CAPTURE_ENV" ]] || usage

printf '%s\n' 'PHASE1EE_CONTROL_SUPPLEMENT_STARTED=YES'

[[ "$CAPTURE_ENV" = /* ]] || safe_fail CAPTURE_ENV_NOT_ABSOLUTE
[[ ! -L "$CAPTURE_ENV" && -f "$CAPTURE_ENV" && -O "$CAPTURE_ENV" ]] || \
  safe_fail CAPTURE_ENV_IDENTITY_INVALID
[[ "$(stat_mode "$CAPTURE_ENV")" = 600 ]] || safe_fail CAPTURE_ENV_MODE_INVALID
CAPTURE_ENV_SHA256_BEFORE="$(sha256sum "$CAPTURE_ENV" | awk '{print $1}')"
# Parse only literal allowlisted assignments; never evaluate the owner-private file.
capture_key_allowed() {
  case "$1" in
    WORKTREE|EXPECTED_COMMIT|PYTHON|EXPECTED_PYTHON_SHA256|\
    PHASE1EE_ATTEMPT_ROOT|LVEF_C3_CONTROL_ROOT|PARENT_CAPACITY_RECEIPT|\
    EXPECTED_PARENT_CAPACITY_RECEIPT_SHA256|EXPECTED_PARENT_CAPACITY_RECEIPT_BYTES|\
    PARENT_CAPACITY_AGGREGATE|EXPECTED_PARENT_CAPACITY_AGGREGATE_SHA256|\
    EXPECTED_PARENT_CAPACITY_AGGREGATE_BYTES|PARENT_CAPACITY_GOVERNING_COMMIT)
      return 0 ;;
    *) return 1 ;;
  esac
}
LVEF_CAPTURE_SEEN_KEYS=$'\n'
while IFS= read -r lvef_line || [[ -n "$lvef_line" ]]; do
  [[ -z "$lvef_line" || "$lvef_line" == \#* ]] && continue
  [[ "$lvef_line" =~ ^([A-Z][A-Z0-9_]*)=([A-Za-z0-9_@%+,./:=-]+)$ ]] || \
    safe_fail CAPTURE_ENV_ASSIGNMENT_NOT_LITERAL
  lvef_key="${BASH_REMATCH[1]}"
  lvef_value="${BASH_REMATCH[2]}"
  capture_key_allowed "$lvef_key" || safe_fail CAPTURE_ENV_KEY_NOT_ALLOWED
  case "$LVEF_CAPTURE_SEEN_KEYS" in
    *$'\n'"$lvef_key"$'\n'*) safe_fail CAPTURE_ENV_KEY_DUPLICATE ;;
  esac
  LVEF_CAPTURE_SEEN_KEYS="${LVEF_CAPTURE_SEEN_KEYS}${lvef_key}"$'\n'
  printf -v "$lvef_key" '%s' "$lvef_value"
done < "$CAPTURE_ENV"
[[ "$(sha256sum "$CAPTURE_ENV" | awk '{print $1}')" = \
  "$CAPTURE_ENV_SHA256_BEFORE" ]] || safe_fail CAPTURE_ENV_CHANGED_DURING_READ

: "${WORKTREE:?}"
: "${EXPECTED_COMMIT:?}"
: "${PYTHON:?}"
: "${EXPECTED_PYTHON_SHA256:?}"
: "${PHASE1EE_ATTEMPT_ROOT:?}"
: "${LVEF_C3_CONTROL_ROOT:?}"
: "${PARENT_CAPACITY_RECEIPT:?}"
: "${EXPECTED_PARENT_CAPACITY_RECEIPT_SHA256:?}"
: "${EXPECTED_PARENT_CAPACITY_RECEIPT_BYTES:?}"
: "${PARENT_CAPACITY_AGGREGATE:?}"
: "${EXPECTED_PARENT_CAPACITY_AGGREGATE_SHA256:?}"
: "${EXPECTED_PARENT_CAPACITY_AGGREGATE_BYTES:?}"
: "${PARENT_CAPACITY_GOVERNING_COMMIT:?}"

[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || safe_fail COMMIT_FORMAT_INVALID
[[ "$PARENT_CAPACITY_GOVERNING_COMMIT" =~ ^[0-9a-f]{40}$ ]] || \
  safe_fail PARENT_COMMIT_FORMAT_INVALID
for lvef_hash in \
  "$EXPECTED_PYTHON_SHA256" \
  "$EXPECTED_PARENT_CAPACITY_RECEIPT_SHA256" \
  "$EXPECTED_PARENT_CAPACITY_AGGREGATE_SHA256"; do
  [[ "$lvef_hash" =~ ^[0-9a-f]{64}$ ]] || safe_fail HASH_FORMAT_INVALID
done
for lvef_count in \
  "$EXPECTED_PARENT_CAPACITY_RECEIPT_BYTES" \
  "$EXPECTED_PARENT_CAPACITY_AGGREGATE_BYTES"; do
  [[ "$lvef_count" =~ ^[0-9]+$ ]] || safe_fail BYTE_COUNT_FORMAT_INVALID
done
for lvef_path in \
  "$WORKTREE" \
  "$PYTHON" \
  "$PHASE1EE_ATTEMPT_ROOT" \
  "$LVEF_C3_CONTROL_ROOT" \
  "$PARENT_CAPACITY_RECEIPT" \
  "$PARENT_CAPACITY_AGGREGATE"; do
  [[ "$lvef_path" = /* ]] || safe_fail REQUIRED_PATH_NOT_ABSOLUTE
done

[[ ! -L "$WORKTREE" && -d "$WORKTREE" && -O "$WORKTREE" ]] || \
  safe_fail WORKTREE_IDENTITY_INVALID
WORKTREE_REAL="$(cd "$WORKTREE" && pwd -P)"
[[ "$(git -C "$WORKTREE_REAL" rev-parse --show-toplevel)" = "$WORKTREE_REAL" ]] || \
  safe_fail WORKTREE_TOPLEVEL_MISMATCH
[[ "$(git -C "$WORKTREE_REAL" branch --show-current)" = "$EXPECTED_BRANCH" ]] || \
  safe_fail BRANCH_MISMATCH
[[ "$(git -C "$WORKTREE_REAL" rev-parse HEAD)" = "$EXPECTED_COMMIT" ]] || \
  safe_fail COMMIT_MISMATCH
[[ -z "$(git -C "$WORKTREE_REAL" status --porcelain --untracked-files=no)" ]] || \
  safe_fail TRACKED_WORKTREE_NOT_CLEAN
for lvef_tracked in \
  configs/lvef_c3_control_tier_policy.json \
  scripts/scc_capture_lvef_c3_post_expansion_capacity.sh \
  scripts/capture_lvef_c3_post_expansion_capacity.py \
  scripts/capture_lvef_c3_live_quota.py; do
  git -C "$WORKTREE_REAL" ls-files --error-unmatch "$lvef_tracked" >/dev/null || \
    safe_fail REQUIRED_SCRIPT_NOT_TRACKED
done
CONTROL_TIER_POLICY="$WORKTREE_REAL/configs/lvef_c3_control_tier_policy.json"
[[ ! -L "$CONTROL_TIER_POLICY" && -f "$CONTROL_TIER_POLICY" ]] || \
  safe_fail CONTROL_POLICY_IDENTITY_INVALID

[[ -f "$PYTHON" && -x "$PYTHON" ]] || \
  safe_fail PYTHON_IDENTITY_INVALID
[[ "$(sha256sum "$PYTHON" | awk '{print $1}')" = "$EXPECTED_PYTHON_SHA256" ]] || \
  safe_fail PYTHON_HASH_MISMATCH
[[ ! -L "$LVEF_C3_CONTROL_ROOT" && -d "$LVEF_C3_CONTROL_ROOT" ]] || \
  safe_fail CONTROL_ROOT_IDENTITY_INVALID

for lvef_parent in "$PARENT_CAPACITY_RECEIPT" "$PARENT_CAPACITY_AGGREGATE"; do
  [[ ! -L "$lvef_parent" && -f "$lvef_parent" && -O "$lvef_parent" ]] || \
    safe_fail PARENT_AUTHORITY_IDENTITY_INVALID
  [[ "$(stat_mode "$lvef_parent")" = 600 ]] || \
    safe_fail PARENT_AUTHORITY_MODE_INVALID
done
[[ "$(stat_follow_size "$PARENT_CAPACITY_RECEIPT")" = \
  "$EXPECTED_PARENT_CAPACITY_RECEIPT_BYTES" ]] || \
  safe_fail PARENT_RECEIPT_SIZE_MISMATCH
[[ "$(sha256sum "$PARENT_CAPACITY_RECEIPT" | awk '{print $1}')" = \
  "$EXPECTED_PARENT_CAPACITY_RECEIPT_SHA256" ]] || \
  safe_fail PARENT_RECEIPT_HASH_MISMATCH
[[ "$(stat_follow_size "$PARENT_CAPACITY_AGGREGATE")" = \
  "$EXPECTED_PARENT_CAPACITY_AGGREGATE_BYTES" ]] || \
  safe_fail PARENT_AGGREGATE_SIZE_MISMATCH
[[ "$(sha256sum "$PARENT_CAPACITY_AGGREGATE" | awk '{print $1}')" = \
  "$EXPECTED_PARENT_CAPACITY_AGGREGATE_SHA256" ]] || \
  safe_fail PARENT_AGGREGATE_HASH_MISMATCH

[[ ! -L "$PHASE1EE_ATTEMPT_ROOT" && -d "$PHASE1EE_ATTEMPT_ROOT" && \
  -O "$PHASE1EE_ATTEMPT_ROOT" ]] || safe_fail ATTEMPT_ROOT_IDENTITY_INVALID
is_private_directory "$PHASE1EE_ATTEMPT_ROOT" || safe_fail ATTEMPT_ROOT_MODE_INVALID
ATTEMPT_ROOT_REAL="$(cd "$PHASE1EE_ATTEMPT_ROOT" && pwd -P)"
case "$ATTEMPT_ROOT_REAL/" in
  "$WORKTREE_REAL/"*) safe_fail ATTEMPT_ROOT_INSIDE_GIT_WORKTREE ;;
esac

ensure_private_directory() {
  local lvef_directory="$1"
  if [[ -L "$lvef_directory" ]]; then
    safe_fail OUTPUT_DIRECTORY_IS_SYMLINK
  elif [[ ! -e "$lvef_directory" ]]; then
    mkdir -- "$lvef_directory"
    chmod 700 "$lvef_directory"
  fi
  [[ -d "$lvef_directory" && -O "$lvef_directory" ]] || \
    safe_fail OUTPUT_DIRECTORY_IDENTITY_INVALID
  is_private_directory "$lvef_directory" || safe_fail OUTPUT_DIRECTORY_MODE_INVALID
}

ensure_private_directory "$ATTEMPT_ROOT_REAL/restricted"
ensure_private_directory "$ATTEMPT_ROOT_REAL/aggregate"
CAPTURE_DIR="$ATTEMPT_ROOT_REAL/restricted/control_capacity_capture"
[[ ! -e "$CAPTURE_DIR" && ! -L "$CAPTURE_DIR" ]] || \
  safe_fail CAPTURE_OUTPUT_COLLISION
mkdir -- "$CAPTURE_DIR"
chmod 700 "$CAPTURE_DIR"
[[ -d "$CAPTURE_DIR" && -O "$CAPTURE_DIR" && ! -L "$CAPTURE_DIR" ]] || \
  safe_fail CAPTURE_DIRECTORY_IDENTITY_INVALID
is_private_directory "$CAPTURE_DIR" || safe_fail CAPTURE_DIRECTORY_MODE_INVALID

RESTRICTED_RECEIPT="$CAPTURE_DIR/post_expansion_control_raw_receipt.json"
AGGREGATE_OUTPUT="$ATTEMPT_ROOT_REAL/aggregate/lvef_c3_post_expansion_capacity.summary.json"
[[ ! -e "$AGGREGATE_OUTPUT" && ! -L "$AGGREGATE_OUTPUT" ]] || \
  safe_fail AGGREGATE_OUTPUT_COLLISION

resolve_tool() {
  local lvef_role="$1"
  local lvef_tool
  lvef_tool="$(type -P "$lvef_role" || true)"
  [[ -n "$lvef_tool" && "$lvef_tool" = /* && -x "$lvef_tool" ]] || \
    safe_fail REQUIRED_READ_ONLY_TOOL_NOT_RESOLVED
  [[ "${lvef_tool##*/}" = "$lvef_role" ]] || \
    safe_fail REQUIRED_READ_ONLY_TOOL_NAME_MISMATCH
  printf '%s' "$lvef_tool"
}

FINDMNT_BIN="$(resolve_tool findmnt)"
DF_BIN="$(resolve_tool df)"
DU_BIN="$(resolve_tool du)"

tool_fingerprint() {
  local lvef_target
  lvef_target="$(readlink -f -- "$1")"
  [[ -f "$lvef_target" && -x "$lvef_target" ]] || \
    safe_fail RESOLVED_TOOL_IDENTITY_INVALID
  printf '%s:%s:%s:%s' \
    "$(sha256sum "$lvef_target" | awk '{print $1}')" \
    "$(stat_follow_size "$lvef_target")" \
    "$(stat_follow_device "$lvef_target")" \
    "$(stat_follow_inode "$lvef_target")"
}

FINDMNT_FINGERPRINT="$(tool_fingerprint "$FINDMNT_BIN")"
DF_FINGERPRINT="$(tool_fingerprint "$DF_BIN")"
DU_FINGERPRINT="$(tool_fingerprint "$DU_BIN")"

capture_one() {
  local lvef_role="$1"
  shift
  local lvef_stdout="$CAPTURE_DIR/$lvef_role.stdout.txt"
  local lvef_stderr="$CAPTURE_DIR/$lvef_role.stderr.txt"
  [[ ! -e "$lvef_stdout" && ! -L "$lvef_stdout" && \
    ! -e "$lvef_stderr" && ! -L "$lvef_stderr" ]] || \
    safe_fail RAW_CAPTURE_COLLISION
  if "$@" >"$lvef_stdout" 2>"$lvef_stderr"; then
    :
  else
    safe_fail READ_ONLY_COMMAND_FAILED
  fi
  chmod 600 "$lvef_stdout" "$lvef_stderr"
  [[ -O "$lvef_stdout" && -O "$lvef_stderr" ]] || \
    safe_fail RAW_CAPTURE_OWNER_INVALID
  [[ "$(stat_mode "$lvef_stdout")" = 600 && \
    "$(stat_mode "$lvef_stderr")" = 600 ]] || \
    safe_fail RAW_CAPTURE_MODE_INVALID
}

capture_one findmnt "$FINDMNT_BIN" --json \
  --target "$LVEF_C3_CONTROL_ROOT" \
  --output SOURCE,TARGET,FSTYPE,OPTIONS
capture_one df "$DF_BIN" -B1 \
  --output=source,size,used,avail,target "$LVEF_C3_CONTROL_ROOT"
capture_one du "$DU_BIN" -x -s -B1 "$LVEF_C3_CONTROL_ROOT"

[[ "$(tool_fingerprint "$FINDMNT_BIN")" = "$FINDMNT_FINGERPRINT" && \
  "$(tool_fingerprint "$DF_BIN")" = "$DF_FINGERPRINT" && \
  "$(tool_fingerprint "$DU_BIN")" = "$DU_FINGERPRINT" ]] || \
  safe_fail TOOL_IDENTITY_CHANGED_DURING_CAPTURE

RECEIPT_BUILD_STDOUT="$CAPTURE_DIR/receipt_builder.stdout.txt"
RECEIPT_BUILD_STDERR="$CAPTURE_DIR/receipt_builder.stderr.txt"
export LVEF_CONTROL_BUILD_CAPTURE_DIR="$CAPTURE_DIR"
export LVEF_CONTROL_BUILD_RECEIPT="$RESTRICTED_RECEIPT"
export LVEF_CONTROL_BUILD_COMMIT="$EXPECTED_COMMIT"
export LVEF_CONTROL_BUILD_PATH="$LVEF_C3_CONTROL_ROOT"
export LVEF_CONTROL_BUILD_PARENT_RECEIPT="$PARENT_CAPACITY_RECEIPT"
export LVEF_CONTROL_BUILD_PARENT_RECEIPT_SHA256="$EXPECTED_PARENT_CAPACITY_RECEIPT_SHA256"
export LVEF_CONTROL_BUILD_PARENT_RECEIPT_BYTES="$EXPECTED_PARENT_CAPACITY_RECEIPT_BYTES"
export LVEF_CONTROL_BUILD_PARENT_AGGREGATE="$PARENT_CAPACITY_AGGREGATE"
export LVEF_CONTROL_BUILD_PARENT_AGGREGATE_SHA256="$EXPECTED_PARENT_CAPACITY_AGGREGATE_SHA256"
export LVEF_CONTROL_BUILD_PARENT_AGGREGATE_BYTES="$EXPECTED_PARENT_CAPACITY_AGGREGATE_BYTES"
export LVEF_CONTROL_BUILD_PARENT_COMMIT="$PARENT_CAPACITY_GOVERNING_COMMIT"
export LVEF_CONTROL_BUILD_FINDMNT_BIN="$FINDMNT_BIN"
export LVEF_CONTROL_BUILD_DF_BIN="$DF_BIN"
export LVEF_CONTROL_BUILD_DU_BIN="$DU_BIN"
if "$PYTHON" - <<'PY' >"$RECEIPT_BUILD_STDOUT" 2>"$RECEIPT_BUILD_STDERR"
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"missing private input: {name}")
    return value


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def raw_spec(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {"path": str(path), "byte_count": len(payload), "sha256": digest(payload)}


def tool_record(role: str, executable: Path, argv: list[str], capture: Path) -> dict[str, object]:
    target = executable.resolve(strict=True)
    metadata = target.stat()
    return {
        "tool_name": role,
        "resolved_executable_path": str(executable),
        "executable_sha256": digest(target.read_bytes()),
        "executable_size_bytes": metadata.st_size,
        "executable_device": metadata.st_dev,
        "executable_inode": metadata.st_ino,
        "argv": argv,
        "argv_sha256": digest(canonical(argv)),
        "exit_status": 0,
        "stdout": raw_spec(capture / f"{role}.stdout.txt"),
        "stderr": raw_spec(capture / f"{role}.stderr.txt"),
    }


capture = Path(required("LVEF_CONTROL_BUILD_CAPTURE_DIR"))
receipt_path = Path(required("LVEF_CONTROL_BUILD_RECEIPT"))
control_path = required("LVEF_CONTROL_BUILD_PATH")
tools = {
    role: Path(required(f"LVEF_CONTROL_BUILD_{role.upper()}_BIN"))
    for role in ("findmnt", "df", "du")
}
argv = {
    "findmnt": [
        str(tools["findmnt"]), "--json", "--target", control_path,
        "--output", "SOURCE,TARGET,FSTYPE,OPTIONS",
    ],
    "df": [
        str(tools["df"]), "-B1", "--output=source,size,used,avail,target",
        control_path,
    ],
    "du": [str(tools["du"]), "-x", "-s", "-B1", control_path],
}
receipt = {
    "schema_version": 1,
    "receipt_kind": "LVEF_C3_POST_EXPANSION_CONTROL_TIER_RAW_COMMAND_RECEIPT",
    "status": "PASS_READ_ONLY_CONTROL_CAPTURE",
    "captured_at_utc": datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "capture_identity": {
        "effective_uid": os.getuid(),
        "effective_username_sha256": digest(pwd.getpwuid(os.getuid()).pw_name.encode("utf-8")),
        "hostname_sha256": digest(socket.gethostname().encode("utf-8")),
        "git_commit": required("LVEF_CONTROL_BUILD_COMMIT"),
    },
    "parent_authority": {
        "restricted_receipt_path": required("LVEF_CONTROL_BUILD_PARENT_RECEIPT"),
        "restricted_receipt_sha256": required("LVEF_CONTROL_BUILD_PARENT_RECEIPT_SHA256"),
        "restricted_receipt_byte_count": int(required("LVEF_CONTROL_BUILD_PARENT_RECEIPT_BYTES")),
        "aggregate_path": required("LVEF_CONTROL_BUILD_PARENT_AGGREGATE"),
        "aggregate_sha256": required("LVEF_CONTROL_BUILD_PARENT_AGGREGATE_SHA256"),
        "aggregate_byte_count": int(required("LVEF_CONTROL_BUILD_PARENT_AGGREGATE_BYTES")),
        "governing_commit": required("LVEF_CONTROL_BUILD_PARENT_COMMIT"),
    },
    "control_path": control_path,
    "commands": {
        role: tool_record(role, tools[role], argv[role], capture)
        for role in ("findmnt", "df", "du")
    },
    "no_mutation_attestations": {
        "quota_changed": False,
        "files_moved": 0,
        "files_deleted": 0,
        "cloud_requests": 0,
        "object_bodies_downloaded": 0,
        "pquota_commands_repeated": 0,
        "research_filesystem_commands_repeated": 0,
    },
}
descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "wb") as handle:
    handle.write((json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    handle.flush()
    os.fsync(handle.fileno())
PY
then
  :
else
  safe_fail RESTRICTED_RECEIPT_BUILD_FAILED
fi
chmod 600 "$RECEIPT_BUILD_STDOUT" "$RECEIPT_BUILD_STDERR" "$RESTRICTED_RECEIPT"

unset LVEF_CONTROL_BUILD_CAPTURE_DIR LVEF_CONTROL_BUILD_RECEIPT
unset LVEF_CONTROL_BUILD_COMMIT LVEF_CONTROL_BUILD_PATH
unset LVEF_CONTROL_BUILD_PARENT_RECEIPT LVEF_CONTROL_BUILD_PARENT_RECEIPT_SHA256
unset LVEF_CONTROL_BUILD_PARENT_RECEIPT_BYTES LVEF_CONTROL_BUILD_PARENT_AGGREGATE
unset LVEF_CONTROL_BUILD_PARENT_AGGREGATE_SHA256
unset LVEF_CONTROL_BUILD_PARENT_AGGREGATE_BYTES LVEF_CONTROL_BUILD_PARENT_COMMIT
unset LVEF_CONTROL_BUILD_FINDMNT_BIN LVEF_CONTROL_BUILD_DF_BIN LVEF_CONTROL_BUILD_DU_BIN

VALIDATOR_STDOUT="$CAPTURE_DIR/capacity_validator.stdout.txt"
VALIDATOR_STDERR="$CAPTURE_DIR/capacity_validator.stderr.txt"
set +e
"$PYTHON" "$WORKTREE_REAL/scripts/capture_lvef_c3_post_expansion_capacity.py" \
  --supplemental-receipt "$RESTRICTED_RECEIPT" \
  --aggregate-output "$AGGREGATE_OUTPUT" \
  --control-policy "$CONTROL_TIER_POLICY" \
  --expected-commit "$EXPECTED_COMMIT" \
  >"$VALIDATOR_STDOUT" 2>"$VALIDATOR_STDERR"
VALIDATOR_STATUS=$?
set -e
chmod 600 "$VALIDATOR_STDOUT" "$VALIDATOR_STDERR"

if [[ "$VALIDATOR_STATUS" -ne 0 ]]; then
  printf '%s\n' 'POST_EXPANSION_CAPACITY_VALIDATION=FAILED'
  printf '%s\n' 'FULL_C3_AUTHORIZED=NO'
  printf '%s\n' 'DICOM_BODY_TRANSFER_AUTHORIZED=NO'
  exit 2
fi
printf '%s\n' 'POST_EXPANSION_CAPACITY_VALIDATION=PASS'
printf '%s\n' 'READ_ONLY_CONTROL_COMMANDS_CAPTURED=3'
printf '%s\n' 'PQUOTA_COMMANDS_REPEATED=0'
printf '%s\n' 'RESEARCH_FILESYSTEM_COMMANDS_REPEATED=0'
printf '%s\n' 'QUOTA_CHANGED=NO'
printf '%s\n' 'FILES_MOVED=NO'
printf '%s\n' 'FILES_DELETED=NO'
printf '%s\n' 'CLOUD_REQUESTS=0'
printf '%s\n' 'OBJECT_BODIES_DOWNLOADED=0'
grep -E '^BACKED_CONTROL_TIER_GATE=(PASS|FAIL)$' "$VALIDATOR_STDOUT"
printf '%s\n' 'FULL_C3_AUTHORIZED=NO'
printf '%s\n' 'DICOM_BODY_TRANSFER_AUTHORIZED=NO'
