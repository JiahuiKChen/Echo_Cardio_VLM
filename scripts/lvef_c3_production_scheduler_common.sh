#!/usr/bin/env bash
# Shared fail-closed SCC runtime checks. Source only from the authority-bound worktree.

lvef_c3_die() {
  printf '%s\n' "C3_PRODUCTION_SCHEDULER_REFUSED=$1" >&2
  exit 78
}

lvef_c3_require_private_regular_file() {
  local candidate="$1"
  [[ -f "$candidate" && ! -L "$candidate" ]] || lvef_c3_die PRIVATE_FILE_NOT_REGULAR
  [[ "$(stat -c '%U' "$candidate")" == "$(id -un)" ]] || lvef_c3_die PRIVATE_FILE_WRONG_OWNER
  [[ "$(stat -c '%a' "$candidate")" == "600" ]] || lvef_c3_die PRIVATE_FILE_WRONG_MODE
}

lvef_c3_require_private_projectnb_directory() {
  local candidate="$1"
  lvef_c3_require_projectnb_path "$candidate"
  [[ -d "$candidate" && ! -L "$candidate" ]] || lvef_c3_die PRIVATE_DIRECTORY_INVALID
  [[ "$(stat -c '%U' "$candidate")" == "$(id -un)" ]] || \
    lvef_c3_die PRIVATE_DIRECTORY_WRONG_OWNER
  [[ "$(stat -c '%a' "$candidate")" == "700" ]] || \
    lvef_c3_die PRIVATE_DIRECTORY_WRONG_MODE
}

lvef_c3_require_projectnb_path() {
  local candidate="$1"
  local cursor=''
  local component
  local -a components
  [[ "$candidate" == /restricted/projectnb/* ]] || lvef_c3_die PATH_OUTSIDE_PROJECTNB
  [[ "$candidate" != *'/../'* && "$candidate" != */.. && "$candidate" != *'/./'* ]] || \
    lvef_c3_die PATH_NOT_CANONICAL
  IFS='/' read -r -a components <<<"${candidate#/}"
  for component in "${components[@]}"; do
    cursor="$cursor/$component"
    if [[ -e "$cursor" || -L "$cursor" ]]; then
      [[ ! -L "$cursor" ]] || lvef_c3_die PATH_HAS_SYMLINK_ANCESTOR
    fi
  done
}

lvef_c3_runtime_key_allowed() {
  case "$1" in
    LVEF_C3_GOVERNING_COMMIT|LVEF_C3_ATTEMPT_ID|\
    LVEF_C3_ORCHESTRATION_CONTRACT|LVEF_C3_ORCHESTRATION_CONTRACT_SHA256|\
    LVEF_C3_BATCH_PLAN|LVEF_C3_BATCH_PLAN_SHA256|LVEF_C3_PRODUCTION_ROOT|\
    LVEF_C3_PYTHON|LVEF_C3_PYTHON_SHA256|LVEF_C3_ENVIRONMENT_RECEIPT|\
    LVEF_C3_ENVIRONMENT_RECEIPT_SHA256|LVEF_C3_CHECKPOINT|\
    LVEF_C3_CHECKPOINT_SHA256|LVEF_C3_GCLOUD_BINARY|LVEF_C3_GCLOUD_BINARY_SHA256|\
    LVEF_C3_GCLOUD_RESOLUTION_RECEIPT|LVEF_C3_GCLOUD_RESOLUTION_RECEIPT_SHA256|\
    LVEF_C3_CLOUDSDK_CONFIG|\
    LVEF_C3_CLOUDSDK_CONFIG_RECEIPT|LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256|\
    LVEF_C3_GCP_BILLING_PROJECT|LVEF_C3_BATCH_LEDGER_ROOT|\
    LVEF_C3_DISPATCH_AUTHORIZATION_ROOT|\
    LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT|LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT|\
    LVEF_C3_EXTRACTION_WORKERS|LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT|\
    LVEF_C3_EMBEDDING_BATCH_SIZE|\
    LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT|\
    LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT|\
    LVEF_C3_FINALIZATION_AUTHORIZATION_ROOT)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

lvef_c3_parse_private_environment() {
  local environment_file="$1"
  local line=''
  local key=''
  local value=''
  local seen=$'\n'
  # This parser intentionally accepts only literal KEY=value assignments.  It
  # does not evaluate shell syntax, command substitutions, variable expansion,
  # quoting, or export statements from the owner-private authority file.
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=([A-Za-z0-9_@%+,./:=-]+)$ ]] || \
      lvef_c3_die ENVIRONMENT_ASSIGNMENT_NOT_LITERAL
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    lvef_c3_runtime_key_allowed "$key" || lvef_c3_die ENVIRONMENT_KEY_NOT_ALLOWED
    [[ "$seen" != *$'\n'"$key"$'\n'* ]] || lvef_c3_die ENVIRONMENT_KEY_DUPLICATE
    seen+="$key"$'\n'
    printf -v "$key" '%s' "$value"
  done < "$environment_file"
}

lvef_c3_load_runtime() {
  local environment_file="$1"
  local canonical_worktree='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
  lvef_c3_require_private_regular_file "$environment_file"
  lvef_c3_parse_private_environment "$environment_file"
  export -n LVEF_C3_GCP_BILLING_PROJECT 2>/dev/null || true
  : "${LVEF_C3_GOVERNING_COMMIT:?}"
  : "${LVEF_C3_ATTEMPT_ID:?}"
  : "${LVEF_C3_ORCHESTRATION_CONTRACT:?}"
  : "${LVEF_C3_ORCHESTRATION_CONTRACT_SHA256:?}"
  : "${LVEF_C3_BATCH_PLAN:?}"
  : "${LVEF_C3_BATCH_PLAN_SHA256:?}"
  : "${LVEF_C3_PRODUCTION_ROOT:?}"
  : "${LVEF_C3_PYTHON:?}"
  : "${LVEF_C3_PYTHON_SHA256:?}"
  : "${LVEF_C3_ENVIRONMENT_RECEIPT:?}"
  : "${LVEF_C3_ENVIRONMENT_RECEIPT_SHA256:?}"
  : "${LVEF_C3_CHECKPOINT:?}"
  : "${LVEF_C3_CHECKPOINT_SHA256:?}"
  : "${LVEF_C3_GCLOUD_BINARY:?}"
  : "${LVEF_C3_GCLOUD_BINARY_SHA256:?}"
  : "${LVEF_C3_GCLOUD_RESOLUTION_RECEIPT:?}"
  : "${LVEF_C3_GCLOUD_RESOLUTION_RECEIPT_SHA256:?}"
  : "${LVEF_C3_CLOUDSDK_CONFIG:?}"
  : "${LVEF_C3_CLOUDSDK_CONFIG_RECEIPT:?}"
  : "${LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256:?}"
  [[ "$LVEF_C3_ATTEMPT_ID" =~ ^lvef_c3_[a-z0-9][a-z0-9_-]{7,95}$ ]] || \
    lvef_c3_die ATTEMPT_ID_INVALID
  [[ "$LVEF_C3_GOVERNING_COMMIT" =~ ^[0-9a-f]{40}$ ]] || \
    lvef_c3_die GOVERNING_COMMIT_INVALID
  [[ -d "$canonical_worktree" && ! -L "$canonical_worktree" ]] || \
    lvef_c3_die AUTHORITY_WORKTREE_INVALID
  [[ "$(git -C "$canonical_worktree" branch --show-current)" == \
    'codex/lvef-multitask-revalidation' ]] || lvef_c3_die AUTHORITY_BRANCH_MISMATCH
  [[ "$(git -C "$canonical_worktree" rev-parse HEAD)" == "$LVEF_C3_GOVERNING_COMMIT" ]] || \
    lvef_c3_die AUTHORITY_COMMIT_MISMATCH
  git -C "$canonical_worktree" diff --quiet -- || lvef_c3_die AUTHORITY_WORKTREE_DIRTY
  git -C "$canonical_worktree" diff --cached --quiet -- || lvef_c3_die AUTHORITY_INDEX_DIRTY
  [[ -x "$LVEF_C3_PYTHON" ]] || lvef_c3_die PYTHON_INVALID
  [[ "$LVEF_C3_PYTHON" == \
    '/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python' ]] || \
    lvef_c3_die PYTHON_NOT_FROZEN_AUTHORITY
  [[ "$LVEF_C3_PYTHON_SHA256" == \
    '1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb' ]] || \
    lvef_c3_die PYTHON_EXPECTED_HASH_INVALID
  [[ "$(sha256sum "$LVEF_C3_PYTHON" | awk '{print $1}')" == "$LVEF_C3_PYTHON_SHA256" ]] || \
    lvef_c3_die PYTHON_HASH_MISMATCH
  [[ -x "$LVEF_C3_GCLOUD_BINARY" ]] || lvef_c3_die GCLOUD_BINARY_INVALID
  [[ "$LVEF_C3_GCLOUD_BINARY" == \
    '/restricted/projectnb/mimicecho/tools/google-cloud-cli-579.0.0/google-cloud-sdk/bin/gcloud' ]] || \
    lvef_c3_die GCLOUD_BINARY_NOT_PINNED
  [[ "$(sha256sum "$LVEF_C3_GCLOUD_BINARY" | awk '{print $1}')" == \
    "$LVEF_C3_GCLOUD_BINARY_SHA256" ]] || lvef_c3_die GCLOUD_BINARY_HASH_MISMATCH
  lvef_c3_require_private_regular_file "$LVEF_C3_GCLOUD_RESOLUTION_RECEIPT"
  [[ "$(sha256sum "$LVEF_C3_GCLOUD_RESOLUTION_RECEIPT" | awk '{print $1}')" == \
    "$LVEF_C3_GCLOUD_RESOLUTION_RECEIPT_SHA256" ]] || \
    lvef_c3_die GCLOUD_RESOLUTION_RECEIPT_HASH_MISMATCH
  [[ -d "$LVEF_C3_CLOUDSDK_CONFIG" && ! -L "$LVEF_C3_CLOUDSDK_CONFIG" ]] || \
    lvef_c3_die CLOUDSDK_CONFIG_INVALID
  [[ "$(stat -c '%U' "$LVEF_C3_CLOUDSDK_CONFIG")" == "$(id -un)" ]] || \
    lvef_c3_die CLOUDSDK_CONFIG_WRONG_OWNER
  [[ "$(stat -c '%a' "$LVEF_C3_CLOUDSDK_CONFIG")" == "700" ]] || \
    lvef_c3_die CLOUDSDK_CONFIG_WRONG_MODE
  export CLOUDSDK_CONFIG="$LVEF_C3_CLOUDSDK_CONFIG"
  lvef_c3_require_private_regular_file \
    "$LVEF_C3_CLOUDSDK_CONFIG/application_default_credentials.json"
  lvef_c3_require_private_regular_file "$LVEF_C3_CLOUDSDK_CONFIG_RECEIPT"
  [[ "$(sha256sum "$LVEF_C3_CLOUDSDK_CONFIG_RECEIPT" | awk '{print $1}')" == \
    "$LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256" ]] || \
    lvef_c3_die CLOUDSDK_CONFIG_RECEIPT_HASH_MISMATCH
  [[ "$LVEF_C3_CLOUDSDK_CONFIG_RECEIPT" == \
    "$LVEF_C3_GCLOUD_RESOLUTION_RECEIPT" ]] || \
    lvef_c3_die CLOUDSDK_AND_GCLOUD_RECEIPT_PATH_MISMATCH
  [[ "$LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256" == \
    "$LVEF_C3_GCLOUD_RESOLUTION_RECEIPT_SHA256" ]] || \
    lvef_c3_die CLOUDSDK_AND_GCLOUD_RECEIPT_HASH_MISMATCH
  [[ -f "$LVEF_C3_ORCHESTRATION_CONTRACT" && ! -L "$LVEF_C3_ORCHESTRATION_CONTRACT" ]] || \
    lvef_c3_die CONTRACT_INVALID
  lvef_c3_require_private_regular_file "$LVEF_C3_BATCH_PLAN"
  lvef_c3_require_private_regular_file "$LVEF_C3_ENVIRONMENT_RECEIPT"
  [[ -f "$LVEF_C3_CHECKPOINT" && ! -L "$LVEF_C3_CHECKPOINT" ]] || \
    lvef_c3_die CHECKPOINT_INVALID
  lvef_c3_require_projectnb_path "$LVEF_C3_PRODUCTION_ROOT"
  [[ -d "$LVEF_C3_PRODUCTION_ROOT" && ! -L "$LVEF_C3_PRODUCTION_ROOT" ]] || \
    lvef_c3_die PRODUCTION_ROOT_INVALID
  [[ "$(stat -c '%U' "$LVEF_C3_PRODUCTION_ROOT")" == "$(id -un)" ]] || \
    lvef_c3_die PRODUCTION_ROOT_WRONG_OWNER
  [[ "$(sha256sum "$LVEF_C3_ORCHESTRATION_CONTRACT" | awk '{print $1}')" == \
    "$LVEF_C3_ORCHESTRATION_CONTRACT_SHA256" ]] || lvef_c3_die CONTRACT_HASH_MISMATCH
  [[ "$(sha256sum "$LVEF_C3_BATCH_PLAN" | awk '{print $1}')" == \
    "$LVEF_C3_BATCH_PLAN_SHA256" ]] || lvef_c3_die BATCH_PLAN_HASH_MISMATCH
  [[ "$(sha256sum "$LVEF_C3_ENVIRONMENT_RECEIPT" | awk '{print $1}')" == \
    "$LVEF_C3_ENVIRONMENT_RECEIPT_SHA256" ]] || lvef_c3_die ENVIRONMENT_HASH_MISMATCH
  [[ "$LVEF_C3_CHECKPOINT_SHA256" == \
    '7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b' ]] || \
    lvef_c3_die CHECKPOINT_EXPECTED_HASH_INVALID
  [[ "$(sha256sum "$LVEF_C3_CHECKPOINT" | awk '{print $1}')" == \
    "$LVEF_C3_CHECKPOINT_SHA256" ]] || lvef_c3_die CHECKPOINT_HASH_MISMATCH
  local authority_root_variable
  for authority_root_variable in \
    LVEF_C3_BATCH_LEDGER_ROOT \
    LVEF_C3_DISPATCH_AUTHORIZATION_ROOT \
    LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT \
    LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT \
    LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT \
    LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT \
    LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT \
    LVEF_C3_FINALIZATION_AUTHORIZATION_ROOT
  do
    [[ -n "${!authority_root_variable:-}" ]] || lvef_c3_die AUTHORITY_ROOT_MISSING
    lvef_c3_require_private_projectnb_directory "${!authority_root_variable}"
  done
  export LVEF_C3_AUTHORITY_WORKTREE="$canonical_worktree"
  export PYTHONDONTWRITEBYTECODE=1
}

lvef_c3_validate_launch_authority() {
  local launch_authority="$1"
  local environment_file="$2"
  lvef_c3_require_private_regular_file "$launch_authority"
  "$LVEF_C3_PYTHON" \
    "$LVEF_C3_AUTHORITY_WORKTREE/scripts/build_lvef_c3_production_launch_authority.py" \
    validate \
    --attempt-id "$LVEF_C3_ATTEMPT_ID" \
    --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
    --execution-environment "$environment_file" \
    --launch-authority "$launch_authority" >/dev/null
}

lvef_c3_bind_job_storage() {
  local stage="$1"
  local batch="$2"
  local job_root="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/jobs/$stage/$batch"
  lvef_c3_require_projectnb_path "$job_root"
  mkdir -p "$job_root/tmp" "$job_root/cache" "$job_root/logs" "$job_root/state" \
    "$job_root/partials"
  export TMPDIR="$job_root/tmp"
  export XDG_CACHE_HOME="$job_root/cache/xdg"
  export TORCH_HOME="$job_root/cache/torch"
  export MPLCONFIGDIR="$job_root/cache/matplotlib"
  export NUMBA_CACHE_DIR="$job_root/cache/numba"
  export JOBLIB_TEMP_FOLDER="$job_root/tmp/joblib"
  export LVEF_C3_JOB_ROOT="$job_root"
  mkdir -p "$XDG_CACHE_HOME" "$TORCH_HOME" "$MPLCONFIGDIR" "$NUMBA_CACHE_DIR" \
    "$JOBLIB_TEMP_FOLDER"
}

lvef_c3_acquire_batch_lock() {
  local stage="$1"
  local batch="$2"
  local lock_root="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/state/active_locks"
  lvef_c3_require_projectnb_path "$lock_root"
  mkdir -p "$lock_root"
  LVEF_C3_ACTIVE_LOCK="$lock_root/${batch}.lock"
  mkdir "$LVEF_C3_ACTIVE_LOCK" 2>/dev/null || lvef_c3_die CONCURRENT_BATCH_OWNERSHIP
  trap 'rmdir "$LVEF_C3_ACTIVE_LOCK" 2>/dev/null || true' EXIT
}

lvef_c3_require_single_active_extraction_cache() {
  local requested_batch="$1"
  local cache_root="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/extracted_cache"
  lvef_c3_require_projectnb_path "$cache_root"
  mkdir -p "$cache_root"
  # Bash globbing (with dotglob+nullglob) enumerates every first-level entry,
  # including symlinks and non-directories; a type-filtered find would hide
  # precisely the unsafe entries this gate must reject.
  (
    local candidate
    local active_count=0
    local candidates=()
    shopt -s dotglob nullglob
    candidates=("$cache_root"/*)
    for candidate in "${candidates[@]}"; do
      [[ ! -L "$candidate" && -d "$candidate" ]] || \
        lvef_c3_die EXTRACTION_CACHE_ENTRY_INVALID
      [[ "$(basename "$candidate")" =~ ^c3_batch_(00[0-9]|01[0-8])$ ]] || \
        lvef_c3_die EXTRACTION_CACHE_ENTRY_INVALID
      local stage_root="$candidate/dicom_extraction"
      local partial_root="$candidate/dicom_extraction.partial"
      local clip_root="$stage_root/clips"
      [[ ! -L "$stage_root" && ! -L "$partial_root" && ! -L "$clip_root" ]] || \
        lvef_c3_die EXTRACTION_CACHE_ENTRY_INVALID
      if [[ -e "$partial_root" || -e "$clip_root" ]]; then
        [[ "$(basename "$candidate")" == "$requested_batch" ]] || \
          lvef_c3_die PRIOR_EXTRACTION_CACHE_NOT_RETIRED
        active_count=$((active_count + 1))
      fi
    done
    (( active_count <= 1 )) || lvef_c3_die MULTIPLE_ACTIVE_EXTRACTION_CACHES
  )
  local guard_root="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/state/active_extraction_guard.lock"
  lvef_c3_require_projectnb_path "$guard_root"
  mkdir "$guard_root" 2>/dev/null || lvef_c3_die CONCURRENT_ACTIVE_EXTRACTION
  LVEF_C3_EXTRACTION_GUARD="$guard_root"
  trap '[[ -z "${LVEF_C3_EXTRACTION_GUARD:-}" ]] || rmdir "$LVEF_C3_EXTRACTION_GUARD" 2>/dev/null || true; [[ -z "${LVEF_C3_ACTIVE_LOCK:-}" ]] || rmdir "$LVEF_C3_ACTIVE_LOCK" 2>/dev/null || true' EXIT
}
