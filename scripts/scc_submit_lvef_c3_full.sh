#!/usr/bin/env bash
# Full C3 submission gate. Current committed contract is intentionally NO-GO.
set -euo pipefail

usage() {
  printf '%s\n' 'usage: scc_submit_lvef_c3_full.sh CONTRACT OWNER_AUTHORIZATION ENV_FILE [--print-only|--submit]'
}

test "$#" -eq 4 || { usage >&2; exit 64; }
CONTRACT="$1"
OWNER_AUTHORIZATION="$2"
ENV_FILE="$3"
MODE="$4"
case "$MODE" in
  --print-only|--submit) ;;
  *) usage >&2; exit 64 ;;
esac

WORKTREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${LVEF_C3_PYTHON:-python3}"
VALIDATION_OUTPUT="$(mktemp "${TMPDIR:-/tmp}/lvef_c3_contract.XXXXXX.json")"
trap 'rm -f "$VALIDATION_OUTPUT"' EXIT

VALIDATION_ARGS=(--contract "$CONTRACT" --output "$VALIDATION_OUTPUT")
if [[ "$MODE" == "--submit" ]]; then
  VALIDATION_ARGS+=(--owner-authorization "$OWNER_AUTHORIZATION" --require-authorized)
elif [[ "$OWNER_AUTHORIZATION" != "-" ]]; then
  VALIDATION_ARGS+=(--owner-authorization "$OWNER_AUTHORIZATION")
fi
set +e
"$PYTHON_BIN" "$WORKTREE/scripts/validate_lvef_c3_execution_contract.py" \
  "${VALIDATION_ARGS[@]}" >/dev/null
VALIDATION_STATUS=$?
set -e
if [[ "$VALIDATION_STATUS" -ne 0 ]]; then
  printf '%s\n' 'FULL_C3_COMMAND_REFUSED: contract structure or authorization gate failed' >&2
  exit 78
fi

# Preparation mode prints the exact future scheduler topology from a valid
# NO-GO contract. Submission additionally requires separate authorization and
# remains disabled until the batch runner and finalizer are implemented,
# validated, and hash-locked. No ambient environment is exported.
BATCH_QSUB_COMMAND=(
  qsub -terse -P mimicecho -N lvef_c3_batch -cwd
  -t 1-19 -tc 1
  -o /restricted/projectnb/mimicecho/lvef_multitask_c3/scheduler_logs
  -e /restricted/projectnb/mimicecho/lvef_multitask_c3/scheduler_logs
  -l h_rt=48:00:00 -l mem_total=64G -l gpus=1 -l gpu_c=8.0 -l gpu_memory=48G
  -v "LVEF_C3_EXECUTION_ENV_FILE=$ENV_FILE"
  "$WORKTREE/scripts/scc_run_lvef_c3_batch.sh"
)
FINAL_QSUB_PREFIX=(
  qsub -terse -P mimicecho -N lvef_c3_finalize -cwd
  -o /restricted/projectnb/mimicecho/lvef_multitask_c3/scheduler_logs
  -e /restricted/projectnb/mimicecho/lvef_multitask_c3/scheduler_logs
  -l h_rt=12:00:00 -l mem_total=32G
)
printf 'proposed_batch_job_id_assignment=BATCH_JOB_ID=$( '
printf '%q ' "${BATCH_QSUB_COMMAND[@]}"
printf ')\n'
printf 'proposed_finalizer_job_id_assignment=FINAL_JOB_ID=$( '
printf '%q ' "${FINAL_QSUB_PREFIX[@]}"
printf '%s ' '-hold_jid "$BATCH_JOB_ID"'
printf '%q ' -v "LVEF_C3_EXECUTION_ENV_FILE=$ENV_FILE" "$WORKTREE/scripts/scc_finalize_lvef_c3_full.sh"
printf ')\n'
if [[ "$MODE" == "--print-only" ]]; then
  printf '%s\n' 'proposed_full_c3_status=NO_GO_PRODUCTION_BATCH_RUNNER_AND_FINALIZER_NOT_IMPLEMENTED'
fi
if [[ "$MODE" == "--submit" ]]; then
  printf '%s\n' 'FULL_C3_SUBMISSION_REFUSED: production job body is intentionally not implemented in Phase 1E-B/C' >&2
  exit 78
fi
