#!/bin/bash -p
# Tracked, manifest-bound Phase 1E-F attempt dispatcher. Offline workflow only.
set -euo pipefail
umask 077

# Trust primitives must not be selected from an inherited PATH.  The SCC
# production host provides the required system utilities in these root-owned
# locations; the workflow's Python executables are separately path- and
# checksum-bound below.
PATH=/usr/bin:/bin
export PATH
hash -r
unset BASH_ENV ENV CDPATH GLOBIGNORE PYTHONHOME PYTHONPATH PYTHONSTARTUP
unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_REPLACE_REF_BASE GIT_EXEC_PATH
unset GIT_CONFIG GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
unset GIT_CONFIG_NOSYSTEM GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM
unset GIT_NAMESPACE GIT_SHALLOW_FILE GIT_QUARANTINE_PATH
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_COUNT=1
GIT_CONFIG_KEY_0=core.fsmonitor
GIT_CONFIG_VALUE_0=false
export GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM GIT_CONFIG_COUNT
export GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE

PHASE1EF_KERNEL="$(/usr/bin/uname -s)"
PHASE1EF_PINNED_EXTERNAL_PYTHON=/share/pkg.8/python3/3.10.12/install/bin/python3.10
PHASE1EF_ECHOPRIME_PYTHON=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
readonly PHASE1EF_KERNEL PHASE1EF_PINNED_EXTERNAL_PYTHON
readonly PHASE1EF_ECHOPRIME_PYTHON

[[ $- = *p* ]] || {
  printf '%s\n' 'PHASE1EF_PRIVILEGED_BASH_STARTUP=REQUIRED' >&2
  exit 65
}

PHASE1EF_STAGE=STARTUP
printf '%s\n' 'PHASE1EF_PRETRANSFER_WRAPPER_STARTED=YES'
printf '%s\n' 'PHASE1EF_TRACKED_ATTEMPT_DISPATCHER_STARTED=YES'
phase1ef_exit_marker() {
  local exit_status="$?"
  trap - EXIT
  printf 'PHASE1EF_PRETRANSFER_EXIT_STATUS=%s\n' "$exit_status"
  printf 'PHASE1EF_PRETRANSFER_LAST_STAGE=%s\n' "$PHASE1EF_STAGE"
  exit "$exit_status"
}
trap phase1ef_exit_marker EXIT

PHASE1EF_STAGE=DISPATCH_MODE
[[ $# -eq 1 ]] || {
  printf '%s\n' 'usage: scc_execute_lvef_c3_phase1ef_attempt.sh --preflight-only|--execute' >&2
  exit 64
}
case "$1" in
  --preflight-only|--execute)
    PHASE1EF_DISPATCH_MODE="$1"
    ;;
  *)
    printf '%s\n' 'PHASE1EF_DISPATCH_MODE=REJECTED' >&2
    exit 64
    ;;
esac

phase1ef_no_symlink_ancestors() {
  local candidate="$1" cursor=/ component
  local -a components=()
  [[ "$candidate" = /* ]] || return 1
  IFS=/ read -r -a components <<<"${candidate#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    cursor="${cursor%/}/$component"
    [[ ! -L "$cursor" ]] || return 1
  done
}

phase1ef_stat_mode() {
  case "$PHASE1EF_KERNEL" in
    Linux) /usr/bin/stat -c '%a' -- "$1" ;;
    Darwin) /usr/bin/stat -f '%Lp' "$1" ;;
    *) return 1 ;;
  esac
}

phase1ef_stat_uid() {
  case "$PHASE1EF_KERNEL" in
    Linux) /usr/bin/stat -c '%u' -- "$1" ;;
    Darwin) /usr/bin/stat -f '%u' "$1" ;;
    *) return 1 ;;
  esac
}

phase1ef_stat_identity() {
  case "$PHASE1EF_KERNEL" in
    Linux) /usr/bin/stat -Lc '%d:%i' -- "$1" ;;
    Darwin) /usr/bin/stat -Lf '%d:%i' "$1" ;;
    *) return 1 ;;
  esac
}

phase1ef_safe_executable_mode() {
  local mode="$1"
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$mode & 07000) == 0 )) || return 1
  (( (8#$mode & 0500) == 0500 )) || return 1
  (( (8#$mode & 0022) == 0 )) || return 1
}

phase1ef_trusted_executable_metadata() {
  local owner_uid="$1" mode="$2"
  [[ "$owner_uid" =~ ^[0-9]+$ ]] || return 1
  [[ "$owner_uid" = "$EUID" || "$owner_uid" = 0 ]] || return 1
  phase1ef_safe_executable_mode "$mode"
}

phase1ef_pinned_external_python_metadata() {
  local candidate="$1" owner_uid="$2" parent_owner_uid="$3"
  local candidate_writable="$4" parent_writable="$5"
  [[ "$candidate" = "$PHASE1EF_PINNED_EXTERNAL_PYTHON" ]] || return 1
  [[ "$owner_uid" =~ ^[0-9]+$ ]] || return 1
  [[ "$owner_uid" != "$EUID" && "$owner_uid" != 0 ]] || return 1
  [[ "$owner_uid" = "$parent_owner_uid" ]] || return 1
  [[ "$candidate_writable" = 0 && "$parent_writable" = 0 ]] || return 1
}

phase1ef_assert_trusted_executable_authority() {
  local candidate="$1" owner_uid mode parent parent_owner_uid
  local candidate_writable=0 parent_writable=0
  [[ -f "$candidate" && ! -L "$candidate" ]] || return 1
  [[ -r "$candidate" && -x "$candidate" ]] || return 1
  phase1ef_no_symlink_ancestors "$candidate" || return 1
  owner_uid="$(phase1ef_stat_uid "$candidate")" || return 1
  mode="$(phase1ef_stat_mode "$candidate")" || return 1
  phase1ef_safe_executable_mode "$mode" || return 1
  if phase1ef_trusted_executable_metadata "$owner_uid" "$mode"; then
    return 0
  fi
  parent="${candidate%/*}"
  [[ -d "$parent" && ! -L "$parent" ]] || return 1
  parent_owner_uid="$(phase1ef_stat_uid "$parent")" || return 1
  if [[ -w "$candidate" ]]; then candidate_writable=1; fi
  if [[ -w "$parent" ]]; then parent_writable=1; fi
  phase1ef_pinned_external_python_metadata \
    "$candidate" "$owner_uid" "$parent_owner_uid" \
    "$candidate_writable" "$parent_writable"
}

phase1ef_sha256() {
  local digest_output digest
  case "$PHASE1EF_KERNEL" in
    Linux) digest_output="$(/usr/bin/sha256sum -- "$1")" ;;
    Darwin) digest_output="$(/usr/bin/openssl dgst -sha256 -r "$1")" ;;
    *) return 1 ;;
  esac
  digest="${digest_output%% *}"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$digest"
}

phase1ef_canonical_nonsymlink_file() {
  local candidate="$1" directory basename physical_directory
  [[ "$candidate" = /* && -f "$candidate" && ! -L "$candidate" ]] || return 1
  phase1ef_no_symlink_ancestors "$candidate" || return 1
  directory="${candidate%/*}"
  basename="${candidate##*/}"
  physical_directory="$(builtin cd -P -- "$directory" && pwd -P)" || return 1
  printf '%s/%s\n' "${physical_directory%/}" "$basename"
}

phase1ef_assert_checkout_clean() {
  local status_line checkout_status
  checkout_status="$(
    /usr/bin/git -C "$1" status --porcelain=v1 --untracked-files=all \
      --ignored=matching
  )" || return 1
  while IFS= read -r status_line || [[ -n "$status_line" ]]; do
    case "$status_line" in
      '') ;;
      '?? .DS_Store'|'?? docs/.DS_Store'|'!! .DS_Store'|'!! docs/.DS_Store') ;;
      *) return 1 ;;
    esac
  done <<<"$checkout_status"
}

readonly -f phase1ef_no_symlink_ancestors phase1ef_stat_mode phase1ef_stat_uid
readonly -f phase1ef_stat_identity phase1ef_sha256
readonly -f phase1ef_canonical_nonsymlink_file phase1ef_assert_checkout_clean
readonly -f phase1ef_safe_executable_mode
readonly -f phase1ef_trusted_executable_metadata
readonly -f phase1ef_pinned_external_python_metadata
readonly -f phase1ef_assert_trusted_executable_authority

phase1ef_private_directory() {
  local candidate="$1" mode
  phase1ef_no_symlink_ancestors "$candidate"
  [[ -d "$candidate" && ! -L "$candidate" && -O "$candidate" ]]
  mode="$(phase1ef_stat_mode "$candidate")"
  [[ "$mode" = 700 || "$mode" = 2700 ]]
}

PHASE1EF_STAGE=RUNNING_DISPATCHER_AUTHORITY
case "${BASH_SOURCE[0]}" in
  /*) phase1ef_invoked_script="${BASH_SOURCE[0]}" ;;
  *) phase1ef_invoked_script="$PWD/${BASH_SOURCE[0]}" ;;
esac
test -f "$phase1ef_invoked_script"
test ! -L "$phase1ef_invoked_script"
test -O "$phase1ef_invoked_script"
phase1ef_no_symlink_ancestors "$phase1ef_invoked_script"
PHASE1EF_RUNNING_SCRIPT="$(
  phase1ef_canonical_nonsymlink_file "$phase1ef_invoked_script"
)"
case "$PHASE1EF_RUNNING_SCRIPT" in
  */scripts/scc_execute_lvef_c3_phase1ef_attempt.sh)
    PHASE1EF_RUNNING_WORKTREE="${PHASE1EF_RUNNING_SCRIPT%/scripts/scc_execute_lvef_c3_phase1ef_attempt.sh}"
    ;;
  *) exit 65 ;;
esac
test -n "$PHASE1EF_RUNNING_WORKTREE"
test "$PHASE1EF_RUNNING_SCRIPT" = \
  "$PHASE1EF_RUNNING_WORKTREE/scripts/scc_execute_lvef_c3_phase1ef_attempt.sh"

# Establish the checkout and closed execution-state authority from the running
# dispatcher before any owner-private value can influence an identity, path,
# or executable selection.
PHASE1EF_STAGE=RUNNING_GIT_AUTHORITY
PHASE1EF_RUNNING_GIT_COMMIT="$(
  /usr/bin/git -C "$PHASE1EF_RUNNING_WORKTREE" rev-parse HEAD
)"
phase1ef_assert_checkout_clean "$PHASE1EF_RUNNING_WORKTREE"
PHASE1EF_EXECUTION_STATE_TOOL="$PHASE1EF_RUNNING_WORKTREE/scripts/lvef_c3_execution_state.py"
PHASE1EF_EXECUTION_STATE_FILE="$PHASE1EF_RUNNING_WORKTREE/configs/lvef_c3_execution_state_v1.yaml"
phase1ef_assert_trusted_executable_authority "$PHASE1EF_PINNED_EXTERNAL_PYTHON"
test "$(phase1ef_sha256 "$PHASE1EF_PINNED_EXTERNAL_PYTHON")" = \
  1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb
test -f "$PHASE1EF_EXECUTION_STATE_TOOL" && test ! -L "$PHASE1EF_EXECUTION_STATE_TOOL"
test -O "$PHASE1EF_EXECUTION_STATE_TOOL"
phase1ef_no_symlink_ancestors "$PHASE1EF_EXECUTION_STATE_TOOL"
phase1ef_safe_executable_mode "$(phase1ef_stat_mode "$PHASE1EF_EXECUTION_STATE_TOOL")"
test -f "$PHASE1EF_EXECUTION_STATE_FILE" && test ! -L "$PHASE1EF_EXECUTION_STATE_FILE"
test -O "$PHASE1EF_EXECUTION_STATE_FILE"
phase1ef_no_symlink_ancestors "$PHASE1EF_EXECUTION_STATE_FILE"
phase1ef_state_mode="$(phase1ef_stat_mode "$PHASE1EF_EXECUTION_STATE_FILE")"
[[ "$phase1ef_state_mode" =~ ^[0-7]{3,4}$ ]]
(( (8#$phase1ef_state_mode & 0022) == 0 ))
test "$(
  "$PHASE1EF_PINNED_EXTERNAL_PYTHON" -I -B "$PHASE1EF_EXECUTION_STATE_TOOL" \
    --state "$PHASE1EF_EXECUTION_STATE_FILE" --check
)" = LVEF_C3_EXECUTION_STATE=PASS
phase1ef_state_field() {
  "$PHASE1EF_PINNED_EXTERNAL_PYTHON" -I -B "$PHASE1EF_EXECUTION_STATE_TOOL" \
    --state "$PHASE1EF_EXECUTION_STATE_FILE" --field "$1"
}
readonly -f phase1ef_state_field
PHASE1EF_STATE_BRANCH="$(phase1ef_state_field branch)"
PHASE1EF_STATE_HISTORICAL_BASE_COMMIT="$(phase1ef_state_field historical_base_commit)"
PHASE1EF_STATE_STARTING_AUTHORITY_COMMIT="$(phase1ef_state_field starting_authority_commit)"
PHASE1EF_STATE_NEXT_EXECUTION_ATTEMPT_ID="$(phase1ef_state_field next_unused_execution_attempt_id)"
PHASE1EF_STATE_NEXT_PRODUCTION_ATTEMPT_ID="$(phase1ef_state_field next_unused_production_attempt_id)"
[[ "$PHASE1EF_STATE_BRANCH" =~ ^[A-Za-z0-9_./-]+$ ]]
[[ "$PHASE1EF_STATE_HISTORICAL_BASE_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$PHASE1EF_STATE_STARTING_AUTHORITY_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$PHASE1EF_STATE_NEXT_EXECUTION_ATTEMPT_ID" =~ ^[a-z0-9_]+$ ]]
[[ "$PHASE1EF_STATE_NEXT_PRODUCTION_ATTEMPT_ID" =~ ^[a-z0-9_]+$ ]]
readonly PHASE1EF_EXECUTION_STATE_TOOL PHASE1EF_EXECUTION_STATE_FILE
readonly PHASE1EF_STATE_BRANCH PHASE1EF_STATE_HISTORICAL_BASE_COMMIT
readonly PHASE1EF_STATE_STARTING_AUTHORITY_COMMIT
readonly PHASE1EF_STATE_NEXT_EXECUTION_ATTEMPT_ID
readonly PHASE1EF_STATE_NEXT_PRODUCTION_ATTEMPT_ID
test "$(/usr/bin/git -C "$PHASE1EF_RUNNING_WORKTREE" branch --show-current)" = \
  "$PHASE1EF_STATE_BRANCH"
test "$(/usr/bin/git -C "$PHASE1EF_RUNNING_WORKTREE" rev-parse "origin/$PHASE1EF_STATE_BRANCH")" = \
  "$PHASE1EF_RUNNING_GIT_COMMIT"
/usr/bin/git -C "$PHASE1EF_RUNNING_WORKTREE" merge-base --is-ancestor \
  "$PHASE1EF_STATE_HISTORICAL_BASE_COMMIT" "$PHASE1EF_RUNNING_GIT_COMMIT"
/usr/bin/git -C "$PHASE1EF_RUNNING_WORKTREE" merge-base --is-ancestor \
  "$PHASE1EF_STATE_STARTING_AUTHORITY_COMMIT" "$PHASE1EF_RUNNING_GIT_COMMIT"

PHASE1EF_STAGE=PRIVATE_ENVIRONMENT_AUTHORITY
: "${PHASE1EF_ENV:?set the owner-private mode-600 Phase 1E-F environment path}"
export -n PHASE1EF_ENV
test -f "$PHASE1EF_ENV" && test ! -L "$PHASE1EF_ENV" && test -O "$PHASE1EF_ENV"
test "$(phase1ef_stat_mode "$PHASE1EF_ENV")" = 600
phase1ef_private_directory "${PHASE1EF_ENV%/*}"
phase1ef_no_symlink_ancestors "$PHASE1EF_ENV"
PHASE1EF_ENV_SHA256="$(phase1ef_sha256 "$PHASE1EF_ENV")"

# The fresh environment is data, not executable shell.  Parse only the exact
# assignment grammar emitted by the tracked preparer and reject duplicate or
# unknown names before assigning a value in this process.
phase1ef_allowed_environment_names=(
  WORKTREE EXPECTED_COMMIT PYTHON PYTHON_AUTHORITY CRC32C_PYTHON GCLOUD
  GCLOUD_RECEIPT CLOUDSDK_CONFIG PRODUCTION_ROOT ATTEMPT_ID
  PHASE1EF_ATTEMPT_ID PHASE1EF_ATTEMPT_ROOT PHASE1EF_AUTHORITY_MANIFEST
  PHASE1EF_AUTHORITY_MANIFEST_SHA256 PHASE1EF_EXECUTION_SCOPES_GRANTED
  ORIGINAL_AGGREGATE_ROOT SUPPLEMENTAL_AGGREGATE_ROOT PRIOR_CAPACITY_PARENT
  PRIOR_CAPACITY_COMPOSITE PRIOR_PRODUCTION_PACKET PRIOR_PRODUCTION_BATCH_PLAN
  SELECTED_STUDIES SELECTED_SOURCE SOURCE_METADATA SPLIT_MAP CHECKPOINT
  PRIOR_ENVIRONMENT_RECEIPT MIGRATION_WITNESS MIGRATION_CLASSIFICATION
  LVEF_C3_GCP_BILLING_PROJECT CHECKPOINT_EXPECTED_SIZE CHECKPOINT_EXPECTED_SHA
  SELECTED_STUDIES_EXPECTED_SIZE SELECTED_STUDIES_EXPECTED_SHA
  SELECTED_SOURCE_EXPECTED_SIZE SELECTED_SOURCE_EXPECTED_SHA
  SOURCE_METADATA_EXPECTED_SIZE SOURCE_METADATA_EXPECTED_SHA
  SPLIT_MAP_EXPECTED_SIZE SPLIT_MAP_EXPECTED_SHA
  PRIOR_ENVIRONMENT_EXPECTED_SIZE PRIOR_ENVIRONMENT_EXPECTED_SHA
  PRIOR_BATCH_PLAN_EXPECTED_SIZE PRIOR_BATCH_PLAN_EXPECTED_SHA
  PRIOR_PRODUCTION_PACKET_EXPECTED_SIZE PRIOR_PRODUCTION_PACKET_EXPECTED_SHA
  PRIOR_CAPACITY_PARENT_EXPECTED_SIZE PRIOR_CAPACITY_PARENT_EXPECTED_SHA
  PRIOR_CAPACITY_COMPOSITE_EXPECTED_SIZE PRIOR_CAPACITY_COMPOSITE_EXPECTED_SHA
  MIGRATION_WITNESS_EXPECTED_SIZE MIGRATION_CLASSIFICATION_EXPECTED_SIZE
  EXPECTED_MIGRATION_WITNESS_SHA256 EXPECTED_MIGRATION_CLASSIFICATION_SHA256
  PRIOR_SAFE_01 PRIOR_SAFE_02 PRIOR_SAFE_03 PRIOR_SAFE_04 PRIOR_SAFE_05
  PRIOR_SAFE_06 PRIOR_SAFE_07 PRIOR_SAFE_08 PRIOR_SAFE_09 PRIOR_SAFE_10
  PRIOR_SAFE_11 PRIOR_SAFE_12 PRIOR_SAFE_01_EXPECTED_SIZE
  PRIOR_SAFE_01_EXPECTED_SHA PRIOR_SAFE_02_EXPECTED_SIZE
  PRIOR_SAFE_02_EXPECTED_SHA PRIOR_SAFE_03_EXPECTED_SIZE
  PRIOR_SAFE_03_EXPECTED_SHA PRIOR_SAFE_04_EXPECTED_SIZE
  PRIOR_SAFE_04_EXPECTED_SHA PRIOR_SAFE_05_EXPECTED_SIZE
  PRIOR_SAFE_05_EXPECTED_SHA PRIOR_SAFE_06_EXPECTED_SIZE
  PRIOR_SAFE_06_EXPECTED_SHA PRIOR_SAFE_07_EXPECTED_SIZE
  PRIOR_SAFE_07_EXPECTED_SHA PRIOR_SAFE_08_EXPECTED_SIZE
  PRIOR_SAFE_08_EXPECTED_SHA PRIOR_SAFE_09_EXPECTED_SIZE
  PRIOR_SAFE_09_EXPECTED_SHA PRIOR_SAFE_10_EXPECTED_SIZE
  PRIOR_SAFE_10_EXPECTED_SHA PRIOR_SAFE_11_EXPECTED_SIZE
  PRIOR_SAFE_11_EXPECTED_SHA PRIOR_SAFE_12_EXPECTED_SIZE
  PRIOR_SAFE_12_EXPECTED_SHA
)
phase1ef_allowed_environment_set='|'
for phase1ef_name in "${phase1ef_allowed_environment_names[@]}"; do
  phase1ef_allowed_environment_set+="${phase1ef_name}|"
done
phase1ef_seen_environment_set='|'
phase1ef_environment_names=()
phase1ef_environment_values=()
while IFS= read -r phase1ef_line || [[ -n "$phase1ef_line" ]]; do
  [[ -n "$phase1ef_line" && "$phase1ef_line" = *=* ]] || exit 65
  phase1ef_name="${phase1ef_line%%=*}"
  phase1ef_value="${phase1ef_line#*=}"
  [[ "$phase1ef_name" =~ ^[A-Z][A-Z0-9_]*$ ]] || exit 65
  [[ "$phase1ef_value" =~ ^[A-Za-z0-9_@%+,./:=-]+$ ]] || exit 65
  case "$phase1ef_allowed_environment_set" in
    *"|${phase1ef_name}|"*) ;;
    *) exit 65 ;;
  esac
  case "$phase1ef_seen_environment_set" in
    *"|${phase1ef_name}|"*) exit 65 ;;
    *) phase1ef_seen_environment_set+="${phase1ef_name}|" ;;
  esac
  phase1ef_environment_names+=("$phase1ef_name")
  phase1ef_environment_values+=("$phase1ef_value")
done <"$PHASE1EF_ENV"
for phase1ef_name in "${phase1ef_allowed_environment_names[@]}"; do
  case "$phase1ef_seen_environment_set" in
    *"|${phase1ef_name}|"*) ;;
    *) exit 65 ;;
  esac
done
for phase1ef_name in "${phase1ef_allowed_environment_names[@]}"; do
  export -n "$phase1ef_name"
done
for phase1ef_index in "${!phase1ef_environment_names[@]}"; do
  phase1ef_name="${phase1ef_environment_names[$phase1ef_index]}"
  printf -v "$phase1ef_name" '%s' \
    "${phase1ef_environment_values[$phase1ef_index]}"
done
test "$(phase1ef_sha256 "$PHASE1EF_ENV")" = "$PHASE1EF_ENV_SHA256"
export -n LVEF_C3_GCP_BILLING_PROJECT

: "${PHASE1EF_ATTEMPT_ID:?canonical attempt binding required}"
: "${PHASE1EF_AUTHORITY_MANIFEST:?canonical manifest path required}"
: "${PHASE1EF_AUTHORITY_MANIFEST_SHA256:?canonical manifest hash required}"
test "$PHASE1EF_EXECUTION_SCOPES_GRANTED" = 0
test "$PHASE1EF_ATTEMPT_ID" = "$PHASE1EF_STATE_NEXT_EXECUTION_ATTEMPT_ID"
test "$ATTEMPT_ID" = "$PHASE1EF_ATTEMPT_ID"
test "$WORKTREE" = "$PHASE1EF_RUNNING_WORKTREE"
test "$PHASE1EF_RUNNING_SCRIPT" = \
  "$WORKTREE/scripts/scc_execute_lvef_c3_phase1ef_attempt.sh"
test "$EXPECTED_COMMIT" = "$PHASE1EF_RUNNING_GIT_COMMIT"
test "$EXPECTED_COMMIT" != "$PHASE1EF_STATE_HISTORICAL_BASE_COMMIT"
[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]]
[[ "$PHASE1EF_AUTHORITY_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]]
test -f "$PHASE1EF_AUTHORITY_MANIFEST"
test ! -L "$PHASE1EF_AUTHORITY_MANIFEST"
test -O "$PHASE1EF_AUTHORITY_MANIFEST"
test "$(phase1ef_stat_mode "$PHASE1EF_AUTHORITY_MANIFEST")" = 600
phase1ef_private_directory "${PHASE1EF_AUTHORITY_MANIFEST%/*}"
phase1ef_no_symlink_ancestors "$PHASE1EF_AUTHORITY_MANIFEST"
test "$(phase1ef_sha256 "$PHASE1EF_AUTHORITY_MANIFEST")" = \
  "$PHASE1EF_AUTHORITY_MANIFEST_SHA256"
test -e "$PYTHON"
test "$PYTHON" = "$PHASE1EF_ECHOPRIME_PYTHON"
phase1ef_no_symlink_ancestors "${PYTHON%/*}"
test -f "$PYTHON_AUTHORITY"
test ! -L "$PYTHON_AUTHORITY"
phase1ef_assert_trusted_executable_authority "$PYTHON_AUTHORITY"
test "$(phase1ef_stat_identity "$PYTHON")" = \
  "$(phase1ef_stat_identity "$PYTHON_AUTHORITY")"
test "$(phase1ef_sha256 "$PYTHON_AUTHORITY")" = \
  1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb
# Preserve the lexical virtual-environment launcher for execution so Python
# retains its environment prefix and installed packages.  The resolved regular
# target remains the byte/checksum authority and the packet role below.

PHASE1EF_STAGE=GIT_AUTHORITY
test "$(/usr/bin/git -C "$WORKTREE" rev-parse HEAD)" = "$EXPECTED_COMMIT"

PHASE1EF_STAGE=CANONICAL_AUTHORITY_MANIFEST
"$PYTHON" -I -B "$WORKTREE/scripts/lvef_c3_phase1ef_authority_manifest.py" validate \
  --worktree "$WORKTREE" --attempt-id "$PHASE1EF_ATTEMPT_ID" \
  --git-branch "$PHASE1EF_STATE_BRANCH" \
  --git-commit "$EXPECTED_COMMIT" \
  --historical-base-commit "$PHASE1EF_STATE_HISTORICAL_BASE_COMMIT" \
  --manifest "$PHASE1EF_AUTHORITY_MANIFEST" \
  --manifest-sha256 "$PHASE1EF_AUTHORITY_MANIFEST_SHA256"
test "$(phase1ef_sha256 "$PHASE1EF_AUTHORITY_MANIFEST")" = \
  "$PHASE1EF_AUTHORITY_MANIFEST_SHA256"

: "${ATTEMPT_ID:?owner-private environment must bind the no-clobber attempt}"
test "$ATTEMPT_ID" = "$PHASE1EF_STATE_NEXT_EXECUTION_ATTEMPT_ID"
PRODUCTION_ATTEMPT_ID="$PHASE1EF_STATE_NEXT_PRODUCTION_ATTEMPT_ID"
test "$PRODUCTION_ROOT" = /restricted/projectnb/mimicecho/lvef_multitask_c3_v2
test "$PHASE1EF_ATTEMPT_ROOT" = \
  "/restricted/projectnb/mimicecho/audits/$ATTEMPT_ID"
BACKUP_CONTAINER="/restricted/project/mimicecho/audits/$ATTEMPT_ID"
BACKUP_ROOT="$BACKUP_CONTAINER/control_backup"
RESTORE_ROOT="$PHASE1EF_ATTEMPT_ROOT/restricted/restore_test"
PRODUCTION_ATTEMPT_ROOT="$PRODUCTION_ROOT/attempts/$PRODUCTION_ATTEMPT_ID"
CAPACITY_ROOT="$PHASE1EF_ATTEMPT_ROOT/restricted/capacity"
CAPACITY_RECEIPT="$CAPACITY_ROOT/post_reallocation_capacity.restricted.json"
CAPACITY_AGGREGATE="$PHASE1EF_ATTEMPT_ROOT/aggregate/lvef_c3_post_reallocation_capacity.summary.json"
AUTHORITY_INPUT_ROOT="$PHASE1EF_ATTEMPT_ROOT/restricted/production_inputs"
ENVIRONMENT_RECEIPT="$AUTHORITY_INPUT_ROOT/production_environment.restricted.json"
BACKUP_AGGREGATE="$PHASE1EF_ATTEMPT_ROOT/aggregate/lvef_c3_backup_recovery.summary.json"
EXECUTION_ENV="$PRODUCTION_ATTEMPT_ROOT/authority/c3_execution_environment.restricted.env"
BATCH_PLAN="$PRODUCTION_ATTEMPT_ROOT/authority/batch_plan.restricted.json"
AUTHORITY_PACKET="$PRODUCTION_ATTEMPT_ROOT/authority/lvef_c3_production_authority_packet.restricted.json"
LAUNCH_AUTHORITY="$PRODUCTION_ATTEMPT_ROOT/authority/lvef_c3_production_launch_authority.restricted.json"
FUTURE_COMMAND="$PRODUCTION_ATTEMPT_ROOT/authority/first_batch_dispatch.unexecuted.sh"
BACKUP_MANIFEST="$BACKUP_ROOT/backup_manifest.restricted.json"
RESTORE_RECEIPT="$BACKUP_ROOT/restore_receipt.restricted.json"
PRETRANSFER_LOCK="$PHASE1EF_ATTEMPT_ROOT/aggregate/lvef_c3_phase1ef_pretransfer_lock.summary.json"
FINAL_LOCK="$PHASE1EF_ATTEMPT_ROOT/aggregate/lvef_c3_phase1ef_final_pretransfer_lock.summary.json"
TERMINAL_SEAL_ROOT="$BACKUP_CONTAINER/terminal_recovery_seal"
TERMINAL_RESTORE_ROOT="$PHASE1EF_ATTEMPT_ROOT/restricted/terminal_recovery_restore"
TERMINAL_SEAL_AGGREGATE="$TERMINAL_SEAL_ROOT/lvef_c3_phase1ef_terminal_recovery_seal.summary.json"
SAFE_OUTPUT_GATE_ROOT="$PHASE1EF_ATTEMPT_ROOT/restricted/safe_output_gate"
SAFE_OUTPUT_GATE_RECEIPT="$SAFE_OUTPUT_GATE_ROOT/phase1ef_live_safe_output_gate.restricted.json"

# Fail before the first mkdir if any intended root or named output collides.
# Absence of the two attempt roots also proves that no unenumerated descendant
# produced by the bounded builders can be inherited from an earlier attempt.
PHASE1EF_STAGE=ALL_OUTPUT_COLLISION_PREFLIGHT
phase1ef_output_paths=(
  "$PHASE1EF_ATTEMPT_ROOT" "$BACKUP_CONTAINER" "$BACKUP_ROOT" "$RESTORE_ROOT"
  "$PRODUCTION_ATTEMPT_ROOT" "$CAPACITY_ROOT" "$CAPACITY_RECEIPT"
  "$CAPACITY_AGGREGATE" "$AUTHORITY_INPUT_ROOT" "$ENVIRONMENT_RECEIPT"
  "$BACKUP_AGGREGATE" "$EXECUTION_ENV" "$BATCH_PLAN" "$AUTHORITY_PACKET"
  "$LAUNCH_AUTHORITY" "$FUTURE_COMMAND" "$BACKUP_MANIFEST" "$RESTORE_RECEIPT"
  "$PRETRANSFER_LOCK" "$FINAL_LOCK" "$TERMINAL_SEAL_ROOT"
  "$TERMINAL_RESTORE_ROOT" "$TERMINAL_SEAL_AGGREGATE"
  "$SAFE_OUTPUT_GATE_ROOT" "$SAFE_OUTPUT_GATE_RECEIPT"
)
for output_path in "${phase1ef_output_paths[@]}"; do
  test ! -e "$output_path" && test ! -L "$output_path"
done
if [[ "$PHASE1EF_DISPATCH_MODE" = --preflight-only ]]; then
  PHASE1EF_STAGE=PREFLIGHT_ONLY_COMPLETED
  printf '%s\n' 'PHASE1EF_TRACKED_DISPATCHER_PREFLIGHT=PASS_ZERO_SCOPE_NO_ROOTS'
  printf '%s\n' 'NEXT_UNUSED_EXECUTION_ATTEMPT_WORKFLOW_INVOKED=NO'
  printf '%s\n' 'CLOUD_REQUESTS=0'
  printf '%s\n' 'QSUB_SUBMISSIONS=0'
  exit 0
fi

PHASE1EF_STAGE=AUTHORIZED_OFFLINE_EXECUTION
mkdir -m 700 -- "$PHASE1EF_ATTEMPT_ROOT"
mkdir -m 700 -- "$BACKUP_CONTAINER"

# One read-only pquota/native-quota/findmnt/df capture. It validates the twelve
# immutable aggregates, Phase 1E-E capacity attempts 001/002, and the prior
# production attempt by hash/schema only. It runs no du, find, storage inventory,
# object listing, or cloud operation.
PHASE1EF_STAGE=CAPACITY_CAPTURE
PHASE1EF_PREEXECUTION_MANIFEST_VALIDATED=YES \
WORKTREE="$WORKTREE" EXPECTED_COMMIT="$EXPECTED_COMMIT" PYTHON="$PYTHON" \
ATTEMPT_ID="$ATTEMPT_ID" PHASE1EF_ATTEMPT_ID="$PHASE1EF_ATTEMPT_ID" \
PHASE1EF_ATTEMPT_ROOT="$PHASE1EF_ATTEMPT_ROOT" \
ORIGINAL_AGGREGATE_ROOT="$ORIGINAL_AGGREGATE_ROOT" \
SUPPLEMENTAL_AGGREGATE_ROOT="$SUPPLEMENTAL_AGGREGATE_ROOT" \
PRIOR_CAPACITY_PARENT="$PRIOR_CAPACITY_PARENT" \
PRIOR_CAPACITY_COMPOSITE="$PRIOR_CAPACITY_COMPOSITE" \
PRIOR_PRODUCTION_PACKET="$PRIOR_PRODUCTION_PACKET" \
"$WORKTREE/scripts/scc_capture_lvef_c3_post_reallocation_capacity.sh"

# Capture the exact current-commit runtime authority before the primary backup.
# This makes the backed witness bind the runtime that governs the rebuilt
# packet, instead of only the historical prior-production runtime receipt.
PHASE1EF_STAGE=CURRENT_ENVIRONMENT_CAPTURE
mkdir -m 700 -- "$AUTHORITY_INPUT_ROOT"
"$PYTHON" "$WORKTREE/scripts/capture_lvef_c3_production_environment.py" \
  --prior-environment "$PRIOR_ENVIRONMENT_RECEIPT" \
  --governing-commit "$EXPECTED_COMMIT" --checkout-root "$WORKTREE" \
  --crc32c-python "$CRC32C_PYTHON" \
  --crc32c-python-expected-sha256 52a2a75599d1bbbd1f5705af946fc3ffbd68b5430adcda0dea2d0a00b33fd1b5 \
  --crc32c-worker "$WORKTREE/scripts/lvef_c3_crc32c_worker.py" \
  --output "$ENVIRONMENT_RECEIPT"

# Every backup artifact argument is ROLE=CLASS=SHA256=SIZE=SCHEMA=PATH.
# Hashes and sizes for immutable inputs came from the private environment;
# identities for freshly generated current-attempt files are captured exactly
# once here. Nothing in this helper prints a private path or file value.
CURRENT_ENVIRONMENT_EXPECTED_SIZE="$(stat -c '%s' -- "$ENVIRONMENT_RECEIPT")"
CURRENT_ENVIRONMENT_EXPECTED_SHA="$(sha256sum -- "$ENVIRONMENT_RECEIPT" | awk '{print $1}')"
CAPACITY_RECEIPT_EXPECTED_SIZE="$(stat -c '%s' -- "$CAPACITY_RECEIPT")"
CAPACITY_RECEIPT_EXPECTED_SHA="$(sha256sum -- "$CAPACITY_RECEIPT" | awk '{print $1}')"
CAPACITY_AGGREGATE_EXPECTED_SIZE="$(stat -c '%s' -- "$CAPACITY_AGGREGATE")"
CAPACITY_AGGREGATE_EXPECTED_SHA="$(sha256sum -- "$CAPACITY_AGGREGATE" | awk '{print $1}')"
artifact_spec() {
  local role="$1" classification="$2" expected_sha="$3" expected_size="$4"
  local schema="$5" source="$6"
  test -f "$source" && test ! -L "$source" && test -O "$source"
  test "$(stat -c '%s' -- "$source")" = "$expected_size"
  test "$(sha256sum -- "$source" | awk '{print $1}')" = "$expected_sha"
  printf '%s=%s=%s=%s=%s=%s' \
    "$role" "$classification" "$expected_sha" "$expected_size" "$schema" "$source"
}

# Bounded owner-private backup plus isolated Git/non-Git restore witness.
PHASE1EF_STAGE=PRIMARY_BACKUP_AND_RESTORE
"$PYTHON" "$WORKTREE/scripts/build_lvef_c3_backup_recovery_witness.py" \
  --policy "$WORKTREE/configs/lvef_c3_backup_recovery_policy_v1.yaml" \
  --attempt-id "$ATTEMPT_ID" --governing-commit "$EXPECTED_COMMIT" \
  --checkout "$WORKTREE" \
  --declaration git_repository=GIT_ORIGIN_PROTECTED \
  --declaration tracked_code_and_configuration=COMMITTED_RECONSTRUCTABLE \
  --declaration echoprime_environment=CHECKSUM_ONLY_NO_COPY_REQUIRED \
  --declaration cloudsdk_runtime=PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE \
  --declaration owner_interactive_authentication=OWNER_RECREATABLE \
  --declaration cloud_credentials=EXCLUDED_CREDENTIAL_MATERIAL \
  --declaration requester_pays_private_environment=EXCLUDED_CREDENTIAL_MATERIAL \
  --artifact "$(artifact_spec checkpoint IRREPLACEABLE_BACKUP_REQUIRED "$CHECKPOINT_EXPECTED_SHA" "$CHECKPOINT_EXPECTED_SIZE" echoprime_checkpoint_v1 "$CHECKPOINT")" \
  --artifact "$(artifact_spec selected_study_manifest IRREPLACEABLE_BACKUP_REQUIRED "$SELECTED_STUDIES_EXPECTED_SHA" "$SELECTED_STUDIES_EXPECTED_SIZE" selected_study_manifest_v1 "$SELECTED_STUDIES")" \
  --artifact "$(artifact_spec selected_source_manifest IRREPLACEABLE_BACKUP_REQUIRED "$SELECTED_SOURCE_EXPECTED_SHA" "$SELECTED_SOURCE_EXPECTED_SIZE" selected_source_manifest_v1 "$SELECTED_SOURCE")" \
  --artifact "$(artifact_spec selected_source_metadata_receipt IRREPLACEABLE_BACKUP_REQUIRED "$SOURCE_METADATA_EXPECTED_SHA" "$SOURCE_METADATA_EXPECTED_SIZE" selected_source_metadata_receipt_v1 "$SOURCE_METADATA")" \
  --artifact "$(artifact_spec split_map IRREPLACEABLE_BACKUP_REQUIRED "$SPLIT_MAP_EXPECTED_SHA" "$SPLIT_MAP_EXPECTED_SIZE" subject_split_map_v1 "$SPLIT_MAP")" \
  --artifact "$(artifact_spec production_environment_receipt IRREPLACEABLE_BACKUP_REQUIRED "$CURRENT_ENVIRONMENT_EXPECTED_SHA" "$CURRENT_ENVIRONMENT_EXPECTED_SIZE" strict_json_mapping_v1 "$ENVIRONMENT_RECEIPT")" \
  --artifact "$(artifact_spec post_reallocation_capacity_receipt IRREPLACEABLE_BACKUP_REQUIRED "$CAPACITY_RECEIPT_EXPECTED_SHA" "$CAPACITY_RECEIPT_EXPECTED_SIZE" strict_json_mapping_v1 "$CAPACITY_RECEIPT")" \
  --artifact "$(artifact_spec post_reallocation_capacity_aggregate IRREPLACEABLE_BACKUP_REQUIRED "$CAPACITY_AGGREGATE_EXPECTED_SHA" "$CAPACITY_AGGREGATE_EXPECTED_SIZE" strict_json_mapping_v1 "$CAPACITY_AGGREGATE")" \
  --artifact "$(artifact_spec historical_migration_witness IRREPLACEABLE_BACKUP_REQUIRED "$EXPECTED_MIGRATION_WITNESS_SHA256" "$MIGRATION_WITNESS_EXPECTED_SIZE" strict_json_mapping_v1 "$MIGRATION_WITNESS")" \
  --artifact "$(artifact_spec historical_migration_classification IRREPLACEABLE_BACKUP_REQUIRED "$EXPECTED_MIGRATION_CLASSIFICATION_SHA256" "$MIGRATION_CLASSIFICATION_EXPECTED_SIZE" strict_json_mapping_v1 "$MIGRATION_CLASSIFICATION")" \
  --artifact "$(artifact_spec prior_production_authority_packet IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_PRODUCTION_PACKET_EXPECTED_SHA" "$PRIOR_PRODUCTION_PACKET_EXPECTED_SIZE" strict_json_mapping_v1 "$PRIOR_PRODUCTION_PACKET")" \
  --artifact "$(artifact_spec production_batch_plan CHECKSUM_ONLY_NO_COPY_REQUIRED "$PRIOR_BATCH_PLAN_EXPECTED_SHA" "$PRIOR_BATCH_PLAN_EXPECTED_SIZE" strict_json_mapping_v1 "$PRIOR_PRODUCTION_BATCH_PLAN")" \
  --artifact "$(artifact_spec prior_safe_aggregate_01 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_01_EXPECTED_SHA" "$PRIOR_SAFE_01_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_01")" \
  --artifact "$(artifact_spec prior_safe_aggregate_02 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_02_EXPECTED_SHA" "$PRIOR_SAFE_02_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_02")" \
  --artifact "$(artifact_spec prior_safe_aggregate_03 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_03_EXPECTED_SHA" "$PRIOR_SAFE_03_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_03")" \
  --artifact "$(artifact_spec prior_safe_aggregate_04 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_04_EXPECTED_SHA" "$PRIOR_SAFE_04_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_04")" \
  --artifact "$(artifact_spec prior_safe_aggregate_05 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_05_EXPECTED_SHA" "$PRIOR_SAFE_05_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_05")" \
  --artifact "$(artifact_spec prior_safe_aggregate_06 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_06_EXPECTED_SHA" "$PRIOR_SAFE_06_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_06")" \
  --artifact "$(artifact_spec prior_safe_aggregate_07 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_07_EXPECTED_SHA" "$PRIOR_SAFE_07_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_07")" \
  --artifact "$(artifact_spec prior_safe_aggregate_08 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_08_EXPECTED_SHA" "$PRIOR_SAFE_08_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_08")" \
  --artifact "$(artifact_spec prior_safe_aggregate_09 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_09_EXPECTED_SHA" "$PRIOR_SAFE_09_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_09")" \
  --artifact "$(artifact_spec prior_safe_aggregate_10 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_10_EXPECTED_SHA" "$PRIOR_SAFE_10_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_10")" \
  --artifact "$(artifact_spec prior_safe_aggregate_11 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_11_EXPECTED_SHA" "$PRIOR_SAFE_11_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_11")" \
  --artifact "$(artifact_spec prior_safe_aggregate_12 IRREPLACEABLE_BACKUP_REQUIRED "$PRIOR_SAFE_12_EXPECTED_SHA" "$PRIOR_SAFE_12_EXPECTED_SIZE" strict_json_or_csv_v1 "$PRIOR_SAFE_12")" \
  --backup-root "$BACKUP_ROOT" --restore-root "$RESTORE_ROOT" \
  --aggregate-output "$BACKUP_AGGREGATE"

# Create a fresh deterministic 19-batch control plane. Prefix exposure of the
# private billing value lasts for this offline subprocess only; it is not argv
# or output.
PHASE1EF_STAGE=PRODUCTION_CONTROL_PLANE
: "${LVEF_C3_GCP_BILLING_PROJECT:?private offline authority required}"
export -n LVEF_C3_GCP_BILLING_PROJECT
LVEF_C3_GCP_BILLING_PROJECT="$LVEF_C3_GCP_BILLING_PROJECT" \
"$PYTHON" "$WORKTREE/scripts/prepare_lvef_c3_production_control_plane.py" \
  --governing-commit "$EXPECTED_COMMIT" --attempt-id "$PRODUCTION_ATTEMPT_ID" \
  --checkout-root "$WORKTREE" \
  --contract "$WORKTREE/configs/lvef_c3_orchestration_v2.yaml" \
  --selected "$SELECTED_STUDIES" --source "$SELECTED_SOURCE" \
  --source-metadata "$SOURCE_METADATA" --split "$SPLIT_MAP" \
  --checkpoint "$CHECKPOINT" --environment-receipt "$ENVIRONMENT_RECEIPT" \
  --crc32c-python "$CRC32C_PYTHON" \
  --crc32c-python-expected-sha256 52a2a75599d1bbbd1f5705af946fc3ffbd68b5430adcda0dea2d0a00b33fd1b5 \
  --crc32c-worker "$WORKTREE/scripts/lvef_c3_crc32c_worker.py" \
  --state-machine-schema "$WORKTREE/configs/lvef_c3_state_machine_v2.json" \
  --resume-ledger-schema "$WORKTREE/configs/lvef_c3_resume_ledger_v2.json" \
  --gcloud-executable "$GCLOUD" \
  --gcloud-resolution-receipt "$GCLOUD_RECEIPT" \
  --cloudsdk-config "$CLOUDSDK_CONFIG" \
  --cloudsdk-config-receipt "$GCLOUD_RECEIPT" \
  --python-sha256 1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb \
  --production-root "$PRODUCTION_ROOT" --extraction-workers 4 \
  --embedding-batch-size 8
unset LVEF_C3_GCP_BILLING_PROJECT

# Freeze the exact future first-batch command before the packet. The two owner
# authorization receipts deliberately do not exist in Phase 1E-F.
PHASE1EF_STAGE=FUTURE_COMMAND_BUILD
"$PYTHON" "$WORKTREE/scripts/build_lvef_c3_first_batch_command.py" \
  --governing-commit "$EXPECTED_COMMIT" --attempt-id "$PRODUCTION_ATTEMPT_ID" \
  --dispatcher "$WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh" \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --dispatch-authorization "$PRODUCTION_ATTEMPT_ROOT/authorizations/dispatch/FIRST_BATCH_DOWNLOAD.1.dispatch_authorization.json" \
  --body-authorization "$PRODUCTION_ATTEMPT_ROOT/authorizations/download/c3_batch_000.authorization.json" \
  --output "$FUTURE_COMMAND"

PHASE1EF_STAGE=PRETRANSFER_COMPOSITE_BUILD
"$PYTHON" "$WORKTREE/scripts/build_lvef_c3_phase1ef_pretransfer_lock.py" \
  --governing-commit "$EXPECTED_COMMIT" --attempt-id "$ATTEMPT_ID" \
  --checkout-root "$WORKTREE" --capacity-receipt "$CAPACITY_RECEIPT" \
  --capacity-aggregate "$CAPACITY_AGGREGATE" --backup-manifest "$BACKUP_MANIFEST" \
  --backup-aggregate "$BACKUP_AGGREGATE" --restore-receipt "$RESTORE_RECEIPT" \
  --future-command "$FUTURE_COMMAND" \
  --all-substantial-writes-bound-to-research YES \
  --backed-control-writes-bounded YES --raw-dicom-deletion-prohibited YES \
  --cache-retirement-requires-separate-authorization YES \
  --output "$PRETRANSFER_LOCK"

# Build exactly 38 roles / 17 semantic gates. The Python launcher is used for
# execution; its resolved regular target is the no-follow packet authority.
PHASE1EF_STAGE=PRODUCTION_AUTHORITY_PACKET_BUILD
"$PYTHON" "$WORKTREE/scripts/build_lvef_c3_production_authority_packet.py" \
  --governing-commit "$EXPECTED_COMMIT" --checkout-root "$WORKTREE" \
  --attempt-id "$PRODUCTION_ATTEMPT_ID" \
  --artifact "aggregate_export_policy=$WORKTREE/configs/lvef_multitask_safe_export_policy.yaml" \
  --artifact "authority_packet_builder=$WORKTREE/scripts/build_lvef_c3_production_authority_packet.py" \
  --artifact "batch_plan=$BATCH_PLAN" \
  --artifact "batch_preservation_producer=$WORKTREE/scripts/preserve_lvef_c3_production_batch.py" \
  --artifact "cache_retirement_gate=$WORKTREE/scripts/retire_lvef_c3_extracted_cache_v2.py" \
  --artifact "checkpoint=$CHECKPOINT" --artifact "cloudsdk_config_receipt=$GCLOUD_RECEIPT" \
  --artifact "control_plane_preparer=$WORKTREE/scripts/prepare_lvef_c3_production_control_plane.py" \
  --artifact "crc32c_python_executable=$CRC32C_PYTHON" \
  --artifact "crc32c_worker=$WORKTREE/scripts/lvef_c3_crc32c_worker.py" \
  --artifact "dicom_audit_and_extractor=$WORKTREE/scripts/lvef_c3_production_stages.py" \
  --artifact "downloader=$WORKTREE/scripts/lvef_c3_orchestration_core.py" \
  --artifact "echoprime_wrapper=$WORKTREE/scripts/lvef_c3_production_stages.py" \
  --artifact "environment_receipt=$ENVIRONMENT_RECEIPT" \
  --artifact "environment_receipt_capture=$WORKTREE/scripts/capture_lvef_c3_production_environment.py" \
  --artifact "execution_environment=$EXECUTION_ENV" \
  --artifact "finalizer=$WORKTREE/scripts/finalize_lvef_c3_production.py" \
  --artifact "future_command_block=$FUTURE_COMMAND" --artifact "gcloud_executable=$GCLOUD" \
  --artifact "gcloud_resolution_receipt=$GCLOUD_RECEIPT" \
  --artifact "launch_authority_builder=$WORKTREE/scripts/build_lvef_c3_production_launch_authority.py" \
  --artifact "orchestration_contract=$WORKTREE/configs/lvef_c3_orchestration_v2.yaml" \
  --artifact "owner_authorization_builder=$WORKTREE/scripts/build_lvef_c3_owner_authorization_receipt.py" \
  --artifact "post_expansion_capacity_summary=$PRETRANSFER_LOCK" \
  --artifact "preservation_policy=$WORKTREE/configs/lvef_c3_preservation_policy_v2.yaml" \
  --artifact "prior_batch_finalization_validator=$WORKTREE/scripts/validate_lvef_c3_prior_batch_finalization.py" \
  --artifact "python_executable=$PYTHON_AUTHORITY" \
  --artifact "resume_ledger_schema=$WORKTREE/configs/lvef_c3_resume_ledger_v2.json" \
  --artifact "scheduler_batch_runner=$WORKTREE/scripts/scc_run_lvef_c3_production_batch_v2.sh" \
  --artifact "scheduler_common=$WORKTREE/scripts/lvef_c3_production_scheduler_common.sh" \
  --artifact "scheduler_dispatch_authorization_validator=$WORKTREE/scripts/validate_lvef_c3_dispatch_authorization.py" \
  --artifact "scheduler_dispatcher=$WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh" \
  --artifact "scheduler_finalizer=$WORKTREE/scripts/scc_finalize_lvef_c3_production_v2.sh" \
  --artifact "selected_source_manifest=$SELECTED_SOURCE" \
  --artifact "selected_source_metadata_receipt=$SOURCE_METADATA" \
  --artifact "selected_study_manifest=$SELECTED_STUDIES" \
  --artifact "split_map=$SPLIT_MAP" \
  --artifact "state_machine_schema=$WORKTREE/configs/lvef_c3_state_machine_v2.json" \
  --output "$AUTHORITY_PACKET"

# The launch envelope is deliberately last and grants zero scopes.
PHASE1EF_STAGE=ZERO_SCOPE_LAUNCH_ENVELOPE_BUILD
"$PYTHON" "$WORKTREE/scripts/build_lvef_c3_production_launch_authority.py" build \
  --attempt-id "$PRODUCTION_ATTEMPT_ID" --governing-commit "$EXPECTED_COMMIT" \
  --execution-environment "$EXECUTION_ENV" --capacity-summary "$PRETRANSFER_LOCK" \
  --authority-packet "$AUTHORITY_PACKET" --launch-authority "$LAUNCH_AUTHORITY"

PHASE1EF_STAGE=FINAL_PRETRANSFER_LOCK_BUILD
"$PYTHON" "$WORKTREE/scripts/finalize_lvef_c3_phase1ef_pretransfer_lock.py" \
  --attempt-id "$ATTEMPT_ID" --governing-commit "$EXPECTED_COMMIT" \
  --capacity-receipt "$CAPACITY_RECEIPT" --capacity-aggregate "$CAPACITY_AGGREGATE" \
  --backup-manifest "$BACKUP_MANIFEST" --backup-aggregate "$BACKUP_AGGREGATE" \
  --restore-receipt "$RESTORE_RECEIPT" --pretransfer-lock "$PRETRANSFER_LOCK" \
  --future-command "$FUTURE_COMMAND" --execution-environment "$EXECUTION_ENV" \
  --production-packet "$AUTHORITY_PACKET" --launch-envelope "$LAUNCH_AUTHORITY" \
  --output "$FINAL_LOCK"

# Terminal, no-cycle recovery seal. The final lock does not bind this seal;
# the seal binds the exact final lock and all six other current authorities.
# The credential-bearing execution environment is validated live but excluded
# from the backup. Both roots are unique and fail closed on any collision.
PHASE1EF_STAGE=TERMINAL_RECOVERY_SEAL_BUILD_AND_RESTORE
test ! -e "$TERMINAL_SEAL_ROOT" && test ! -L "$TERMINAL_SEAL_ROOT"
test ! -e "$TERMINAL_RESTORE_ROOT" && test ! -L "$TERMINAL_RESTORE_ROOT"
test ! -e "$TERMINAL_SEAL_AGGREGATE" && test ! -L "$TERMINAL_SEAL_AGGREGATE"
"$PYTHON" "$WORKTREE/scripts/build_lvef_c3_terminal_recovery_seal.py" \
  --attempt-id "$ATTEMPT_ID" --governing-commit "$EXPECTED_COMMIT" \
  --checkout-root "$WORKTREE" --current-environment "$ENVIRONMENT_RECEIPT" \
  --backup-aggregate "$BACKUP_AGGREGATE" \
  --pretransfer-composite "$PRETRANSFER_LOCK" \
  --authority-packet "$AUTHORITY_PACKET" --launch-envelope "$LAUNCH_AUTHORITY" \
  --future-command "$FUTURE_COMMAND" --final-lock "$FINAL_LOCK" \
  --execution-environment "$EXECUTION_ENV" --seal-root "$TERMINAL_SEAL_ROOT" \
  --restore-root "$TERMINAL_RESTORE_ROOT" \
  --aggregate-output "$TERMINAL_SEAL_AGGREGATE"

# Re-run the default launch validator after the terminal seal exists. The
# validator derives the sibling final lock and terminal-seal aggregate and
# rejects any live-chain hash mismatch; no execution scope is granted.
PHASE1EF_STAGE=TERMINAL_CHAIN_LAUNCH_REVALIDATION
"$PYTHON" "$WORKTREE/scripts/build_lvef_c3_production_launch_authority.py" validate \
  --attempt-id "$PRODUCTION_ATTEMPT_ID" --governing-commit "$EXPECTED_COMMIT" \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY"

# Apply the committed closed-schema safe-output profiles to the five live
# aggregate byte streams. This writes a restricted receipt only; it neither
# exports nor mutates any candidate aggregate.
PHASE1EF_STAGE=LIVE_AGGREGATE_SAFE_OUTPUT_GATE
mkdir -m 700 -- "$SAFE_OUTPUT_GATE_ROOT"
"$PYTHON" "$WORKTREE/scripts/validate_lvef_c3_phase1ef_safe_outputs.py" \
  --attempt-id "$ATTEMPT_ID" --governing-commit "$EXPECTED_COMMIT" \
  --policy "$WORKTREE/configs/lvef_multitask_safe_export_policy.yaml" \
  --artifact "capacity=$CAPACITY_AGGREGATE" \
  --artifact "backup=$BACKUP_AGGREGATE" \
  --artifact "pretransfer=$PRETRANSFER_LOCK" \
  --artifact "final=$FINAL_LOCK" \
  --artifact "terminal=$TERMINAL_SEAL_AGGREGATE" \
  --receipt "$SAFE_OUTPUT_GATE_RECEIPT"

PHASE1EF_STAGE=COMPLETED
printf '%s\n' 'PHASE1EF_POST_REALLOCATION_LOCK=PASS_OFFLINE_ZERO_SCOPE'
printf '%s\n' 'TERMINAL_RECOVERY_SEAL=PASS_CURRENT_CHAIN_BACKED_AND_RESTORED'
printf '%s\n' 'LIVE_AGGREGATE_SAFE_OUTPUT_GATE=PASS_FIVE_OF_FIVE'
printf '%s\n' 'CLOUD_REQUESTS=0'
printf '%s\n' 'OBJECT_LISTING_REPEATED=NO'
printf '%s\n' 'STORAGE_INVENTORY_REPEATED=NO'
printf '%s\n' 'SCHEDULER_SUBMISSIONS=0'
printf '%s\n' 'DICOM_BODIES_DOWNLOADED=NO'
printf '%s\n' 'FULL_C3_STATUS=GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION'
