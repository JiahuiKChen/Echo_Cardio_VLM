#!/usr/bin/env bash
# Deliberately disabled production-batch interface for the future 19-task array.
set -euo pipefail
: "${LVEF_C3_EXECUTION_ENV_FILE:?LVEF_C3_EXECUTION_ENV_FILE is required}"
: "${SGE_TASK_ID:?SGE_TASK_ID is required}"
[[ "$SGE_TASK_ID" =~ ^[0-9]+$ ]]
if (( SGE_TASK_ID < 1 || SGE_TASK_ID > 19 )); then
  printf '%s\n' 'FULL_C3_BATCH_REFUSED: scheduler task is outside the locked 1-19 range' >&2
  exit 78
fi
printf '%s\n' 'FULL_C3_BATCH_REFUSED: production batch runner is not implemented or authorized in Phase 1E-B/C' >&2
exit 78
