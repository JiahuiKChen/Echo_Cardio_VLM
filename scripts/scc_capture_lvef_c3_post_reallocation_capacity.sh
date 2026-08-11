#!/usr/bin/env bash
# Run the fixed read-only Phase 1E-F capacity capture from an owner-private env.
set -euo pipefail
umask 077

[[ $# -eq 1 ]] || { printf '%s\n' 'usage: scc_capture_lvef_c3_post_reallocation_capacity.sh ENV' >&2; exit 64; }
LVEF_ENV="$1"
[[ "$LVEF_ENV" = /* && -f "$LVEF_ENV" && ! -L "$LVEF_ENV" && -O "$LVEF_ENV" ]] || exit 65
[[ "$(stat -c '%a' "$LVEF_ENV")" = 600 ]] || exit 65
LVEF_ENV_SHA="$(sha256sum -- "$LVEF_ENV" | awk '{print $1}')"
source "$LVEF_ENV"
[[ "$(sha256sum -- "$LVEF_ENV" | awk '{print $1}')" = "$LVEF_ENV_SHA" ]] || exit 65

: "${WORKTREE:?}" "${EXPECTED_COMMIT:?}" "${PYTHON:?}" "${PHASE1EF_ATTEMPT_ROOT:?}"
: "${ORIGINAL_AGGREGATE_ROOT:?}" "${SUPPLEMENTAL_AGGREGATE_ROOT:?}"
: "${PRIOR_CAPACITY_PARENT:?}" "${PRIOR_CAPACITY_COMPOSITE:?}" "${PRIOR_PRODUCTION_PACKET:?}"

exec "$PYTHON" "$WORKTREE/scripts/capture_lvef_c3_post_reallocation_capacity.py" \
  --attempt-id lvef_multitask_phase1ef_post_reallocation_lock_attempt_002 \
  --governing-commit "$EXPECTED_COMMIT" --checkout "$WORKTREE" \
  --attempt-root "$PHASE1EF_ATTEMPT_ROOT" \
  --research-path /restricted/projectnb/mimicecho \
  --backed-path /restricted/project/mimicecho \
  --original-aggregate-root "$ORIGINAL_AGGREGATE_ROOT" \
  --supplemental-aggregate-root "$SUPPLEMENTAL_AGGREGATE_ROOT" \
  --prior-capacity-parent "$PRIOR_CAPACITY_PARENT" \
  --prior-capacity-composite "$PRIOR_CAPACITY_COMPOSITE" \
  --prior-production-packet "$PRIOR_PRODUCTION_PACKET"
