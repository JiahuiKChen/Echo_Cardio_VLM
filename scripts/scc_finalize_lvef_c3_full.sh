#!/usr/bin/env bash
# Deliberately disabled full-cohort finalization interface.
set -euo pipefail
: "${LVEF_C3_EXECUTION_ENV_FILE:?LVEF_C3_EXECUTION_ENV_FILE is required}"
printf '%s\n' 'FULL_C3_FINALIZATION_REFUSED: production finalizer is not implemented or authorized in Phase 1E-B/C' >&2
exit 78
