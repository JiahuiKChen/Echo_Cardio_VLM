#!/bin/bash -p
set -euo pipefail
umask 077

export PATH=/usr/bin:/bin
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_TERMINAL_PROMPT=0
export PYTHONDONTWRITEBYTECODE=1
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
unset PYTHONPATH PYTHONHOME PYTHONINSPECT PYTHONSTARTUP
unset CLOUDSDK_CONFIG GOOGLE_APPLICATION_CREDENTIALS LVEF_C3_GCP_BILLING_PROJECT

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
ECHOPRIME_PYTHON=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
EXPECTED_PYTHON_SHA256=1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb
[[ $# -eq 6 ]] || exit 64
AUTHORITY_PATH=$1
STAGE_ID=$2
EXPECTED_GOVERNING_COMMIT=$3
EXPECTED_RUN_ID=$4
EXPECTED_LAUNCHER_SHA256=$5
EXPECTED_WORKER_SHA256=$6
case "$STAGE_ID" in
  DOWNLOAD|DICOM_EXTRACTION|ECHOPRIME_EMBEDDING|BATCH_PRESERVATION|CANARY_FINALIZATION) ;;
  *) exit 64 ;;
esac
[[ "$AUTHORITY_PATH" == /restricted/projectnb/mimicecho/lvef_multitask_c3_v2/owner_private/exact_five_canary/execution_authorization_v1.json ]] || exit 65
[[ "${JOB_ID:-}" =~ ^[0-9]+$ ]] || exit 65
[[ "$EXPECTED_GOVERNING_COMMIT" =~ ^[0-9a-f]{40}$ ]] || exit 65
[[ "$EXPECTED_RUN_ID" =~ ^lvef_c3_exact_five_canary_[a-z0-9]{8}$ ]] || exit 65
[[ "$EXPECTED_LAUNCHER_SHA256" =~ ^[0-9a-f]{64}$ ]] || exit 65
[[ "$EXPECTED_WORKER_SHA256" =~ ^[0-9a-f]{64}$ ]] || exit 65
[[ -d "$WORKTREE" && ! -L "$WORKTREE" ]] || exit 65

# qsub may run long after dispatch. Re-authenticate the spooled launcher, the
# delayed worker pathname, and the complete tracked tree before Python imports
# any project module. The dispatcher seals these existing hashes in every
# immutable command ledger; this is a queue-delay recheck, not new authority.
read -r observed_launcher_sha _ < <(/usr/bin/sha256sum -- "${BASH_SOURCE[0]}" 2>/dev/null) || exit 65
[[ "$observed_launcher_sha" == "$EXPECTED_LAUNCHER_SHA256" ]] || exit 65

cursor=/
IFS=/ read -r -a launcher_parts <<< "${ECHOPRIME_PYTHON#/}"
for ((index=0; index < ${#launcher_parts[@]} - 1; index++)); do
  cursor="${cursor%/}/${launcher_parts[index]}"
  [[ -d "$cursor" && ! -L "$cursor" ]] || exit 65
done
[[ -e "$ECHOPRIME_PYTHON" && ( -f "$ECHOPRIME_PYTHON" || -L "$ECHOPRIME_PYTHON" ) ]] || exit 65
resolved_python=$(/usr/bin/readlink -f -- "$ECHOPRIME_PYTHON" 2>/dev/null) || exit 65
[[ -n "$resolved_python" && -f "$resolved_python" && ! -L "$resolved_python" ]] || exit 65
resolved_mode=$(/usr/bin/stat -c %a -- "$resolved_python" 2>/dev/null) || exit 65
[[ "$resolved_mode" =~ ^[0-7]{3,4}$ ]] || exit 65
(( (8#$resolved_mode & 07000) == 0 && (8#$resolved_mode & 0500) == 0500 && (8#$resolved_mode & 0022) == 0 )) || exit 65
read -r resolved_sha _ < <(/usr/bin/sha256sum -- "$resolved_python" 2>/dev/null) || exit 65
[[ "$resolved_sha" = "$EXPECTED_PYTHON_SHA256" ]] || exit 65

# Establish a trusted, commit-exact lifecycle reconciler before the remaining
# queued-job prerequisites. Any later bootstrap or Python-worker failure is a
# terminal no-retry failure, recorded through the same canonical state API.
STATE_MODULE="$WORKTREE/scripts/lvef_c3_canary_state.py"
STATE_LOADER="$WORKTREE/scripts/lvef_c3_execution_state.py"
EXECUTION_STATE="$WORKTREE/configs/lvef_c3_execution_state_v1.yaml"
for trusted_state_path in "$STATE_MODULE" "$STATE_LOADER" "$EXECUTION_STATE"; do
  [[ -f "$trusted_state_path" && ! -L "$trusted_state_path" ]] || exit 65
done
/usr/bin/git -C "$WORKTREE" diff-index --quiet "$EXPECTED_GOVERNING_COMMIT" -- \
  scripts/lvef_c3_canary_state.py scripts/lvef_c3_execution_state.py \
  configs/lvef_c3_execution_state_v1.yaml || exit 65
untracked_scripts=$(/usr/bin/git -C "$WORKTREE" ls-files --others -- scripts) || exit 65
while IFS= read -r untracked_script; do
  case "$untracked_script" in
    ""|scripts/__pycache__/*) ;;
    *) exit 65 ;;
  esac
done <<< "$untracked_scripts"

terminalize_bootstrap_failure() {
  local exit_status=$?
  trap - EXIT
  if (( exit_status != 0 )); then
    "$ECHOPRIME_PYTHON" -I -B -X pycache_prefix=/dev/null/lvef_c3_canary \
      "$STATE_MODULE" --transition \
      --state-root /restricted/projectnb/mimicecho/lvef_multitask_c3_v2/owner_private/exact_five_canary/lifecycle_state \
      --execution-state "$EXECUTION_STATE" \
      --governing-commit "$EXPECTED_GOVERNING_COMMIT" \
      --run-id "$EXPECTED_RUN_ID" \
      --expected-current CANARY_EXECUTING \
      --target-state CANARY_TERMINAL_FAIL \
      --reason-code CANARY_STAGE_BOOTSTRAP_FAILED_NO_RETRY \
      >/dev/null 2>&1 || true
  fi
  exit "$exit_status"
}
trap terminalize_bootstrap_failure EXIT

worker_path="$WORKTREE/scripts/lvef_c3_canary_stage_worker.py"
[[ -f "$worker_path" && ! -L "$worker_path" ]] || exit 65
[[ -f "$AUTHORITY_PATH" && ! -L "$AUTHORITY_PATH" ]] || exit 65
authority_mode=$(/usr/bin/stat -c %a -- "$AUTHORITY_PATH" 2>/dev/null) || exit 65
[[ "$authority_mode" == 600 ]] || exit 65
worker_before=$(/usr/bin/stat -c '%d:%i:%s:%Y:%f:%u' -- "$worker_path" 2>/dev/null) || exit 65
worker_mode=$(/usr/bin/stat -c %a -- "$worker_path" 2>/dev/null) || exit 65
[[ "$worker_mode" =~ ^[0-7]{3,4}$ ]] || exit 65
(( (8#$worker_mode & 0022) == 0 )) || exit 65
read -r observed_worker_sha _ < <(/usr/bin/sha256sum -- "$worker_path" 2>/dev/null) || exit 65
worker_after=$(/usr/bin/stat -c '%d:%i:%s:%Y:%f:%u' -- "$worker_path" 2>/dev/null) || exit 65
[[ "$worker_before" == "$worker_after" ]] || exit 65
[[ "$observed_worker_sha" == "$EXPECTED_WORKER_SHA256" ]] || exit 65
observed_head=$(/usr/bin/git -C "$WORKTREE" rev-parse HEAD 2>/dev/null) || exit 65
[[ "$observed_head" == "$EXPECTED_GOVERNING_COMMIT" ]] || exit 65
/usr/bin/git -C "$WORKTREE" diff-index --quiet "$EXPECTED_GOVERNING_COMMIT" -- || exit 65

if "$ECHOPRIME_PYTHON" -I -B -X pycache_prefix=/dev/null/lvef_c3_canary \
    "$WORKTREE/scripts/lvef_c3_canary_stage_worker.py" \
    --scope-authority "$AUTHORITY_PATH" --stage "$STAGE_ID" \
    --scheduler-job-identity "$JOB_ID"; then
  trap - EXIT
  exit 0
else
  worker_status=$?
  exit "$worker_status"
fi
