#!/usr/bin/env bash
# Stage-separated future C3 dispatcher. Phase 1E-E did not execute this script.
set -euo pipefail
umask 077

usage() {
  printf '%s\n' 'usage: dispatcher --print-only|--submit STAGE ENV_FILE LAUNCH_AUTHORITY|- DISPATCH_RECEIPT|- HOLD_JOB_ID|- TASK_SCOPE'
}
[[ "$#" -eq 7 ]] || { usage >&2; exit 64; }
MODE="$1"
STAGE="$2"
ENV_FILE="$3"
LAUNCH_AUTHORITY="$4"
DISPATCH_RECEIPT="$5"
HOLD_JOB_ID="$6"
TASK_SCOPE="$7"
case "$MODE" in --print-only|--submit) ;; *) usage >&2; exit 64 ;; esac
case "$STAGE" in
  FIRST_BATCH_DOWNLOAD|REMAINING_BATCH_DOWNLOAD|DICOM_EXTRACTION|ECHOPRIME_EMBEDDING|BATCH_PRESERVATION|CACHE_RETIREMENT|PRESERVATION_FINALIZATION) ;;
  *) usage >&2; exit 64 ;;
esac
[[ "$HOLD_JOB_ID" == '-' || "$HOLD_JOB_ID" =~ ^[0-9]+([,][0-9]+)*$ ]] || {
  printf '%s\n' 'C3_DISPATCH_REFUSED=INVALID_HOLD_JOB_ID' >&2; exit 78;
}
case "$STAGE" in
  FIRST_BATCH_DOWNLOAD)
    [[ "$TASK_SCOPE" == 1 ]] || { printf '%s\n' 'C3_DISPATCH_REFUSED=FIRST_BATCH_SCOPE_INVALID' >&2; exit 78; }
    ;;
  REMAINING_BATCH_DOWNLOAD)
    [[ "$TASK_SCOPE" =~ ^([2-9]|1[0-9])$ ]] || { printf '%s\n' 'C3_DISPATCH_REFUSED=REMAINING_BATCH_SCOPE_INVALID' >&2; exit 78; }
    ;;
  DICOM_EXTRACTION|ECHOPRIME_EMBEDDING|BATCH_PRESERVATION|CACHE_RETIREMENT)
    [[ "$TASK_SCOPE" =~ ^([1-9]|1[0-9])$ ]] || { printf '%s\n' 'C3_DISPATCH_REFUSED=ROLLING_BATCH_SCOPE_INVALID' >&2; exit 78; }
    ;;
  PRESERVATION_FINALIZATION)
    [[ "$TASK_SCOPE" == none ]] || { printf '%s\n' 'C3_DISPATCH_REFUSED=FINAL_SCOPE_INVALID' >&2; exit 78; }
    ;;
esac

AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
COMMON="$AUTHORITY_WORKTREE/scripts/lvef_c3_production_scheduler_common.sh"
[[ -f "$COMMON" && ! -L "$COMMON" ]] || { printf '%s\n' 'C3_DISPATCH_REFUSED=COMMON_HELPER_INVALID' >&2; exit 78; }
# shellcheck disable=SC1090
source "$COMMON"
lvef_c3_load_runtime "$ENV_FILE"
if [[ "$MODE" == '--submit' ]]; then
  [[ "$LAUNCH_AUTHORITY" != '-' ]] || lvef_c3_die LAUNCH_AUTHORITY_ABSENT
  lvef_c3_validate_launch_authority "$LAUNCH_AUTHORITY" "$ENV_FILE"
  : "${LVEF_C3_DISPATCH_AUTHORIZATION_ROOT:?}"
  lvef_c3_require_private_projectnb_directory "$LVEF_C3_DISPATCH_AUTHORIZATION_ROOT"
  EXPECTED_DISPATCH_RECEIPT="$LVEF_C3_DISPATCH_AUTHORIZATION_ROOT/$STAGE.$TASK_SCOPE.dispatch_authorization.json"
  [[ "$DISPATCH_RECEIPT" == "$EXPECTED_DISPATCH_RECEIPT" ]] || \
    lvef_c3_die DISPATCH_AUTHORIZATION_PATH_MISMATCH
  lvef_c3_require_private_regular_file "$DISPATCH_RECEIPT"
  "$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/validate_lvef_c3_dispatch_authorization.py" \
    --receipt "$DISPATCH_RECEIPT" --stage "$STAGE" \
    --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
    --attempt-id "$LVEF_C3_ATTEMPT_ID" \
    --orchestration-contract "$LVEF_C3_ORCHESTRATION_CONTRACT" \
    --batch-plan "$LVEF_C3_BATCH_PLAN" --execution-environment "$ENV_FILE" \
    --launch-authority "$LAUNCH_AUTHORITY" \
    --authorized-array-range "$TASK_SCOPE" >/dev/null
fi

TAG="$(printf '%s' "$LVEF_C3_ATTEMPT_ID:$STAGE:$TASK_SCOPE" | sha256sum | cut -c1-12)"
LOG_ROOT="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/scheduler_logs/$STAGE/$TASK_SCOPE"
WORK_ROOT="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/scheduler_work/$STAGE/$TASK_SCOPE"
lvef_c3_require_projectnb_path "$LOG_ROOT"
lvef_c3_require_projectnb_path "$WORK_ROOT"
if [[ "$MODE" == '--submit' ]]; then
  mkdir -p "$LOG_ROOT" "$WORK_ROOT"
fi
case "$STAGE" in
  FIRST_BATCH_DOWNLOAD)
    JOB_NAME="c3_dl1_$TAG"
    RUNNER="$AUTHORITY_WORKTREE/scripts/scc_run_lvef_c3_production_batch_v2.sh"
    RESOURCE_ARGS=(-l h_rt=24:00:00 -l mem_total=16G)
    USE_ARRAY=YES
    ;;
  REMAINING_BATCH_DOWNLOAD)
    JOB_NAME="c3_dlr_$TAG"
    RUNNER="$AUTHORITY_WORKTREE/scripts/scc_run_lvef_c3_production_batch_v2.sh"
    RESOURCE_ARGS=(-l h_rt=24:00:00 -l mem_total=16G)
    USE_ARRAY=YES
    ;;
  DICOM_EXTRACTION)
    JOB_NAME="c3_ext_$TAG"
    RUNNER="$AUTHORITY_WORKTREE/scripts/scc_run_lvef_c3_production_batch_v2.sh"
    RESOURCE_ARGS=(-l h_rt=48:00:00 -l mem_total=64G)
    USE_ARRAY=YES
    ;;
  ECHOPRIME_EMBEDDING)
    JOB_NAME="c3_emb_$TAG"
    RUNNER="$AUTHORITY_WORKTREE/scripts/scc_run_lvef_c3_production_batch_v2.sh"
    RESOURCE_ARGS=(-l h_rt=24:00:00 -l mem_total=64G -l gpus=1 -l gpu_c=8.0 -l gpu_memory=48G)
    USE_ARRAY=YES
    ;;
  BATCH_PRESERVATION)
    JOB_NAME="c3_pre_$TAG"
    RUNNER="$AUTHORITY_WORKTREE/scripts/scc_run_lvef_c3_production_batch_v2.sh"
    RESOURCE_ARGS=(-l h_rt=12:00:00 -l mem_total=32G)
    USE_ARRAY=YES
    ;;
  CACHE_RETIREMENT)
    JOB_NAME="c3_ret_$TAG"
    RUNNER="$AUTHORITY_WORKTREE/scripts/scc_run_lvef_c3_production_batch_v2.sh"
    RESOURCE_ARGS=(-l h_rt=12:00:00 -l mem_total=16G)
    USE_ARRAY=YES
    ;;
  PRESERVATION_FINALIZATION)
    JOB_NAME="c3_fin_$TAG"
    RUNNER="$AUTHORITY_WORKTREE/scripts/scc_finalize_lvef_c3_production_v2.sh"
    RESOURCE_ARGS=(-l h_rt=12:00:00 -l mem_total=32G)
    USE_ARRAY=NO
    ;;
esac
[[ -f "$RUNNER" && ! -L "$RUNNER" ]] || lvef_c3_die SCHEDULER_RUNNER_INVALID
QSUB_COMMAND=(
  qsub -terse -P mimicecho -N "$JOB_NAME"
  -wd "$WORK_ROOT" -o "$LOG_ROOT" -e "$LOG_ROOT"
)
if [[ "$HOLD_JOB_ID" != '-' ]]; then
  QSUB_COMMAND+=(-hold_jid "$HOLD_JOB_ID")
fi
if [[ "$USE_ARRAY" == YES ]]; then
  QSUB_COMMAND+=(-t "$TASK_SCOPE" -tc 1)
fi
QSUB_COMMAND+=(
  "${RESOURCE_ARGS[@]}" -v "LVEF_C3_SCHEDULER_STAGE=$STAGE"
  "$RUNNER" "$ENV_FILE" "$LAUNCH_AUTHORITY"
)
if [[ "$MODE" == '--print-only' ]]; then
  printf '%s\n' 'C3_FUTURE_DISPATCHER=UNEXECUTED'
  printf 'C3_FUTURE_QSUB_COMMAND='
  printf '%q ' "${QSUB_COMMAND[@]}"
  printf '\n'
  printf '%s\n' 'QSUB_EXECUTED=NO'
  exit 0
fi
JOB_ID="$("${QSUB_COMMAND[@]}")"
[[ "$JOB_ID" =~ ^[0-9]+([.][0-9-]+:[0-9]+)?$ ]] || lvef_c3_die SCHEDULER_JOB_ID_INVALID
printf '%s\n' 'C3_DISPATCH_SUBMISSION=PASS' >&2
printf '%s\n' "$JOB_ID"
