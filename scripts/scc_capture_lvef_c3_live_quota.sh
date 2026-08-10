#!/usr/bin/env bash
# Capture and validate one read-only SCC live-quota evidence bundle.
set -euo pipefail
umask 077

EXPECTED_BRANCH='codex/lvef-multitask-revalidation'
CAPTURE_ENV=''

safe_fail() {
  printf '%s\n' "PHASE1ED_LIVE_QUOTA_CAPTURE=FAILED_$1"
  exit 2
}

usage() {
  printf '%s\n' 'usage: scc_capture_lvef_c3_live_quota.sh --capture-env FILE' >&2
  exit 64
}

stat_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
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

printf '%s\n' 'PHASE1ED_LIVE_QUOTA_CAPTURE_STARTED=YES'

[[ "$CAPTURE_ENV" = /* ]] || safe_fail CAPTURE_ENV_NOT_ABSOLUTE
[[ ! -L "$CAPTURE_ENV" && -f "$CAPTURE_ENV" && -O "$CAPTURE_ENV" ]] || \
  safe_fail CAPTURE_ENV_IDENTITY_INVALID
[[ "$(stat_mode "$CAPTURE_ENV")" = '600' ]] || \
  safe_fail CAPTURE_ENV_MODE_INVALID
CAPTURE_ENV_SHA256_BEFORE="$(sha256sum "$CAPTURE_ENV" | awk '{print $1}')"
# The input is an owner-created, mode-600 shell-assignment authority file.
# It is never printed, copied, or included in an aggregate artifact.
source "$CAPTURE_ENV"
[[ "$(sha256sum "$CAPTURE_ENV" | awk '{print $1}')" = \
  "$CAPTURE_ENV_SHA256_BEFORE" ]] || safe_fail CAPTURE_ENV_CHANGED_DURING_READ

: "${WORKTREE:?}"
: "${EXPECTED_COMMIT:?}"
: "${PYTHON:?}"
: "${EXPECTED_PYTHON_SHA256:?}"
: "${PHASE1ED_ATTEMPT_ROOT:?}"
: "${LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT:?}"
: "${LVEF_C3_LIVE_QUOTA_PRINCIPAL:?}"
: "${MIGRATION_WITNESS:?}"
: "${EXPECTED_MIGRATION_WITNESS_SHA256:?}"
: "${MIGRATION_CLASSIFICATION:?}"
: "${EXPECTED_MIGRATION_CLASSIFICATION_SHA256:?}"

[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || safe_fail COMMIT_FORMAT_INVALID
[[ "$EXPECTED_PYTHON_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
  safe_fail PYTHON_HASH_FORMAT_INVALID
[[ "$EXPECTED_MIGRATION_WITNESS_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
  safe_fail MIGRATION_WITNESS_HASH_FORMAT_INVALID
[[ "$EXPECTED_MIGRATION_CLASSIFICATION_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
  safe_fail MIGRATION_CLASSIFICATION_HASH_FORMAT_INVALID
[[ "$LVEF_C3_LIVE_QUOTA_PRINCIPAL" =~ ^[A-Za-z0-9._-]+$ ]] || \
  safe_fail QUOTA_PRINCIPAL_FORMAT_INVALID
for lvef_path in \
  "$WORKTREE" \
  "$PYTHON" \
  "$PHASE1ED_ATTEMPT_ROOT" \
  "$LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT" \
  "$MIGRATION_WITNESS" \
  "$MIGRATION_CLASSIFICATION"; do
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
git -C "$WORKTREE_REAL" ls-files --error-unmatch \
  scripts/scc_capture_lvef_c3_live_quota.sh >/dev/null || \
  safe_fail CAPTURE_WRAPPER_NOT_TRACKED
git -C "$WORKTREE_REAL" ls-files --error-unmatch \
  scripts/capture_lvef_c3_live_quota.py >/dev/null || \
  safe_fail QUOTA_VALIDATOR_NOT_TRACKED

[[ -f "$PYTHON" && -x "$PYTHON" ]] || \
  safe_fail PYTHON_IDENTITY_INVALID
[[ "$(sha256sum "$PYTHON" | awk '{print $1}')" = "$EXPECTED_PYTHON_SHA256" ]] || \
  safe_fail PYTHON_HASH_MISMATCH
for lvef_evidence in "$MIGRATION_WITNESS" "$MIGRATION_CLASSIFICATION"; do
  [[ ! -L "$lvef_evidence" && -f "$lvef_evidence" && -O "$lvef_evidence" ]] || \
    safe_fail MIGRATION_EVIDENCE_IDENTITY_INVALID
  [[ "$(stat_mode "$lvef_evidence")" = '600' ]] || \
    safe_fail MIGRATION_EVIDENCE_MODE_INVALID
done
[[ "$(sha256sum "$MIGRATION_WITNESS" | awk '{print $1}')" = \
  "$EXPECTED_MIGRATION_WITNESS_SHA256" ]] || safe_fail MIGRATION_WITNESS_HASH_MISMATCH
[[ "$(sha256sum "$MIGRATION_CLASSIFICATION" | awk '{print $1}')" = \
  "$EXPECTED_MIGRATION_CLASSIFICATION_SHA256" ]] || \
  safe_fail MIGRATION_CLASSIFICATION_HASH_MISMATCH

[[ ! -L "$PHASE1ED_ATTEMPT_ROOT" && -d "$PHASE1ED_ATTEMPT_ROOT" && \
  -O "$PHASE1ED_ATTEMPT_ROOT" ]] || safe_fail ATTEMPT_ROOT_IDENTITY_INVALID
[[ "$(stat_mode "$PHASE1ED_ATTEMPT_ROOT")" = '700' ]] || \
  safe_fail ATTEMPT_ROOT_MODE_INVALID
ATTEMPT_ROOT_REAL="$(cd "$PHASE1ED_ATTEMPT_ROOT" && pwd -P)"
case "$ATTEMPT_ROOT_REAL/" in
  "$WORKTREE_REAL/"*) safe_fail ATTEMPT_ROOT_INSIDE_GIT_WORKTREE ;;
esac
[[ ! -L "$LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT" && \
  -d "$LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT" ]] || \
  safe_fail RESEARCH_ROOT_IDENTITY_INVALID

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
  [[ "$(stat_mode "$lvef_directory")" = '700' ]] || \
    safe_fail OUTPUT_DIRECTORY_MODE_INVALID
}

ensure_private_directory "$ATTEMPT_ROOT_REAL/restricted"
ensure_private_directory "$ATTEMPT_ROOT_REAL/aggregate"
CAPTURE_DIR="$ATTEMPT_ROOT_REAL/restricted/live_quota_capture"
[[ ! -e "$CAPTURE_DIR" && ! -L "$CAPTURE_DIR" ]] || \
  safe_fail CAPTURE_OUTPUT_COLLISION
mkdir -- "$CAPTURE_DIR"
chmod 700 "$CAPTURE_DIR"

RESTRICTED_RECEIPT="$CAPTURE_DIR/live_quota_raw_receipt.json"
AGGREGATE_OUTPUT="$ATTEMPT_ROOT_REAL/aggregate/lvef_c3_live_quota.summary.json"
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

PQUOTA_BIN="$(resolve_tool pquota)"
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

PQUOTA_FINGERPRINT="$(tool_fingerprint "$PQUOTA_BIN")"
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
  [[ "$(stat_mode "$lvef_stdout")" = '600' && \
    "$(stat_mode "$lvef_stderr")" = '600' ]] || \
    safe_fail RAW_CAPTURE_MODE_INVALID
}

capture_one pquota "$PQUOTA_BIN" -u "$LVEF_C3_LIVE_QUOTA_PRINCIPAL"
capture_one findmnt "$FINDMNT_BIN" --json \
  --target "$LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT" \
  --output SOURCE,TARGET,FSTYPE,OPTIONS
capture_one df "$DF_BIN" -B1 \
  --output=source,size,used,avail,target "$LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT"
capture_one du "$DU_BIN" -x -s -B1 "$LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT"

[[ "$(tool_fingerprint "$PQUOTA_BIN")" = "$PQUOTA_FINGERPRINT" && \
  "$(tool_fingerprint "$FINDMNT_BIN")" = "$FINDMNT_FINGERPRINT" && \
  "$(tool_fingerprint "$DF_BIN")" = "$DF_FINGERPRINT" && \
  "$(tool_fingerprint "$DU_BIN")" = "$DU_FINGERPRINT" ]] || \
  safe_fail TOOL_IDENTITY_CHANGED_DURING_CAPTURE

RECEIPT_BUILD_STDOUT="$CAPTURE_DIR/receipt_builder.stdout.txt"
RECEIPT_BUILD_STDERR="$CAPTURE_DIR/receipt_builder.stderr.txt"
export LVEF_QUOTA_BUILD_CAPTURE_DIR="$CAPTURE_DIR"
export LVEF_QUOTA_BUILD_RECEIPT="$RESTRICTED_RECEIPT"
export LVEF_QUOTA_BUILD_COMMIT="$EXPECTED_COMMIT"
export LVEF_QUOTA_BUILD_RESEARCH_ROOT="$LVEF_C3_LIVE_QUOTA_RESEARCH_ROOT"
export LVEF_QUOTA_BUILD_PRINCIPAL="$LVEF_C3_LIVE_QUOTA_PRINCIPAL"
export LVEF_QUOTA_BUILD_MIGRATION_WITNESS="$MIGRATION_WITNESS"
export LVEF_QUOTA_BUILD_MIGRATION_WITNESS_SHA256="$EXPECTED_MIGRATION_WITNESS_SHA256"
export LVEF_QUOTA_BUILD_MIGRATION_CLASSIFICATION="$MIGRATION_CLASSIFICATION"
export LVEF_QUOTA_BUILD_MIGRATION_CLASSIFICATION_SHA256="$EXPECTED_MIGRATION_CLASSIFICATION_SHA256"
export LVEF_QUOTA_BUILD_PQUOTA_BIN="$PQUOTA_BIN"
export LVEF_QUOTA_BUILD_FINDMNT_BIN="$FINDMNT_BIN"
export LVEF_QUOTA_BUILD_DF_BIN="$DF_BIN"
export LVEF_QUOTA_BUILD_DU_BIN="$DU_BIN"
if "$PYTHON" - <<'PY' >"$RECEIPT_BUILD_STDOUT" 2>"$RECEIPT_BUILD_STDERR"
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"missing private input: {name}")
    return value


def raw_spec(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {"path": str(path), "byte_count": len(payload), "sha256": digest(payload)}


def tool_record(role: str, executable: Path, argv: list[str], capture: Path) -> dict[str, object]:
    target = executable.resolve(strict=True)
    metadata = target.stat()
    payload = target.read_bytes()
    return {
        "tool_name": role,
        "resolved_executable_path": str(executable),
        "executable_sha256": digest(payload),
        "executable_size_bytes": metadata.st_size,
        "executable_device": metadata.st_dev,
        "executable_inode": metadata.st_ino,
        "argv": argv,
        "argv_sha256": digest(canonical(argv)),
        "exit_status": 0,
        "stdout": raw_spec(capture / f"{role}.stdout.txt"),
        "stderr": raw_spec(capture / f"{role}.stderr.txt"),
    }


capture = Path(required("LVEF_QUOTA_BUILD_CAPTURE_DIR"))
receipt_path = Path(required("LVEF_QUOTA_BUILD_RECEIPT"))
commit = required("LVEF_QUOTA_BUILD_COMMIT")
research = required("LVEF_QUOTA_BUILD_RESEARCH_ROOT")
principal = required("LVEF_QUOTA_BUILD_PRINCIPAL")
witness = Path(required("LVEF_QUOTA_BUILD_MIGRATION_WITNESS"))
classification = Path(required("LVEF_QUOTA_BUILD_MIGRATION_CLASSIFICATION"))

tools = {
    role: Path(required(f"LVEF_QUOTA_BUILD_{role.upper()}_BIN"))
    for role in ("pquota", "findmnt", "df", "du")
}
argv = {
    "pquota": [str(tools["pquota"]), "-u", principal],
    "findmnt": [
        str(tools["findmnt"]), "--json", "--target", research,
        "--output", "SOURCE,TARGET,FSTYPE,OPTIONS",
    ],
    "df": [
        str(tools["df"]), "-B1", "--output=source,size,used,avail,target", research,
    ],
    "du": [str(tools["du"]), "-x", "-s", "-B1", research],
}

pquota_lines = [
    line.split()
    for line in (capture / "pquota.stdout.txt").read_text(encoding="utf-8").splitlines()
    if line.split() and line.split()[0] == f"/rprojectnb/{principal}"
]
if len(pquota_lines) != 1 or len(pquota_lines[0]) != 5:
    raise SystemExit("restricted pquota research row was not unique and exact")
_, quota_display, quota_files, usage_display, usage_files = pquota_lines[0]
quota_decimal = Decimal(quota_display) * 1_000_000_000
if quota_decimal != quota_decimal.to_integral_value():
    raise SystemExit("restricted pquota allocation was not integral decimal bytes")

findmnt = json.loads((capture / "findmnt.stdout.txt").read_text(encoding="utf-8"))
rows = findmnt.get("filesystems") if isinstance(findmnt, dict) else None
if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
    raise SystemExit("restricted findmnt row was not unique")
filesystem = rows[0]
filesystem_authority = digest(canonical({
    "source": filesystem.get("source"),
    "target": filesystem.get("target"),
    "fstype": filesystem.get("fstype"),
}))

receipt = {
    "schema_version": 2,
    "receipt_kind": "LVEF_C3_LIVE_RESEARCH_QUOTA_RAW_COMMAND_RECEIPT",
    "status": "PASS_READ_ONLY_CAPTURE",
    "captured_at_utc": datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "capture_identity": {
        "effective_uid": os.getuid(),
        "effective_username_sha256": digest(pwd.getpwuid(os.getuid()).pw_name.encode("utf-8")),
        "hostname_sha256": digest(socket.gethostname().encode("utf-8")),
        "git_commit": commit,
    },
    "unit_authority": {
        "quota_allocation_unit_system": "DECIMAL_SI",
        "decimal_gb_bytes": 1_000_000_000,
        "decimal_tb_bytes": 1_000_000_000_000,
        "pquota_allocation_display_is_exact": True,
        "pquota_usage_display_may_be_rounded": True,
        "pquota_usage_used_as_exact": False,
        "exact_project_usage_source": "DU_X_S_B1_ALLOCATED_BYTES",
    },
    "research_path": research,
    "commands": {
        role: tool_record(role, tools[role], argv[role], capture)
        for role in ("pquota", "findmnt", "df", "du")
    },
    "pquota_research_mapping": {
        "quota_principal": principal,
        "research_filesystem_row": f"/rprojectnb/{principal}",
        "research_row_role": "RESEARCH_NOT_BACKED_UP",
        "quota_display_value": quota_display,
        "quota_display_unit": "GB",
        "quota_files_display_value": quota_files,
        "usage_display_value": usage_display,
        "usage_display_unit": "GB",
        "usage_files_display_value": usage_files,
        "file_count_columns_used_for_bytes": False,
        "quota_bytes": int(quota_decimal),
        "usage_display_is_rounded": True,
        "usage_display_used_as_exact": False,
        "mapped_filesystem_authority_sha256": filesystem_authority,
    },
    "resource_plan": {
        "selected_source_bytes": 1_216_569_133_322,
        "frozen_projected_peak_bytes": 1_611_642_076_332,
        "required_headroom_bytes": 200_000_000_000,
        "minimum_effective_quota_bytes": 1_811_642_076_332,
    },
    "migration_authority": {
        "migration_state": "PLANNED_NOT_EXECUTED",
        "migration_witness_path": str(witness),
        "migration_witness_sha256": required("LVEF_QUOTA_BUILD_MIGRATION_WITNESS_SHA256"),
        "migration_classification_path": str(classification),
        "migration_classification_sha256": required("LVEF_QUOTA_BUILD_MIGRATION_CLASSIFICATION_SHA256"),
        "migration_completion_verified": False,
        "backup_verified": False,
    },
    "no_mutation_attestations": {
        "quota_changed": False,
        "files_moved": 0,
        "files_deleted": 0,
        "cloud_requests": 0,
        "object_bodies_downloaded": 0,
    },
}

flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
descriptor = os.open(receipt_path, flags, 0o600)
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

unset LVEF_QUOTA_BUILD_CAPTURE_DIR LVEF_QUOTA_BUILD_RECEIPT
unset LVEF_QUOTA_BUILD_COMMIT LVEF_QUOTA_BUILD_RESEARCH_ROOT
unset LVEF_QUOTA_BUILD_PRINCIPAL LVEF_QUOTA_BUILD_MIGRATION_WITNESS
unset LVEF_QUOTA_BUILD_MIGRATION_WITNESS_SHA256
unset LVEF_QUOTA_BUILD_MIGRATION_CLASSIFICATION
unset LVEF_QUOTA_BUILD_MIGRATION_CLASSIFICATION_SHA256
unset LVEF_QUOTA_BUILD_PQUOTA_BIN LVEF_QUOTA_BUILD_FINDMNT_BIN
unset LVEF_QUOTA_BUILD_DF_BIN LVEF_QUOTA_BUILD_DU_BIN

VALIDATOR_STDOUT="$CAPTURE_DIR/quota_validator.stdout.txt"
VALIDATOR_STDERR="$CAPTURE_DIR/quota_validator.stderr.txt"
set +e
"$PYTHON" "$WORKTREE_REAL/scripts/capture_lvef_c3_live_quota.py" \
  --restricted-receipt "$RESTRICTED_RECEIPT" \
  --aggregate-output "$AGGREGATE_OUTPUT" \
  --expected-commit "$EXPECTED_COMMIT" \
  >"$VALIDATOR_STDOUT" 2>"$VALIDATOR_STDERR"
VALIDATOR_STATUS=$?
set -e
chmod 600 "$VALIDATOR_STDOUT" "$VALIDATOR_STDERR"

printf '%s\n' 'PHASE1ED_LIVE_QUOTA_AUTHORITY_GATE=PASS'
printf '%s\n' 'READ_ONLY_QUOTA_COMMANDS_CAPTURED=4'
printf '%s\n' 'QUOTA_CHANGED=NO'
printf '%s\n' 'FILES_MOVED=NO'
printf '%s\n' 'FILES_DELETED=NO'
printf '%s\n' 'CLOUD_REQUESTS=0'
printf '%s\n' 'OBJECT_BODIES_DOWNLOADED=0'
if [[ "$VALIDATOR_STATUS" -eq 3 ]]; then
  printf '%s\n' 'LIVE_QUOTA_CAPTURE_VALIDATION=PASS'
  printf '%s\n' 'MINIMUM_EFFECTIVE_QUOTA_GATE=FAIL'
  printf '%s\n' 'PHASE1ED_LIVE_QUOTA_OPERATIONAL_RESULT=PASS_CAPTURED_NO_GO'
  printf '%s\n' 'DICOM_BODY_TRANSFER_AUTHORIZED=NO'
  exit 0
elif [[ "$VALIDATOR_STATUS" -eq 0 ]]; then
  printf '%s\n' 'LIVE_QUOTA_CAPTURE_VALIDATION=PASS'
  printf '%s\n' 'MINIMUM_EFFECTIVE_QUOTA_GATE=PASS'
  printf '%s\n' 'PHASE1ED_LIVE_QUOTA_OPERATIONAL_RESULT=PASS_CAPTURED_MIGRATION_STILL_BLOCKED'
  printf '%s\n' 'DICOM_BODY_TRANSFER_AUTHORIZED=NO'
  exit 0
fi
printf '%s\n' 'LIVE_QUOTA_CAPTURE_VALIDATION=FAILED'
printf '%s\n' 'MINIMUM_EFFECTIVE_QUOTA_GATE=UNPROVEN'
printf '%s\n' 'DICOM_BODY_TRANSFER_AUTHORIZED=NO'
exit 2
