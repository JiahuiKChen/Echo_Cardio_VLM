#!/bin/bash -p
# Run fixed read-only capacity capture from the manifest-bound dispatcher.
set -euo pipefail
umask 077

[[ $# -eq 0 ]] || {
  printf '%s\n' 'usage: scc_capture_lvef_c3_post_reallocation_capacity.sh' >&2
  exit 64
}

# The manifest-bound tracked dispatcher parses the private environment as
# literal data and passes only this wrapper's required values.  Never re-source
# the owner-private file here: doing so would re-open command execution and a
# time-of-check/time-of-use gap after the dispatcher's authority gate.
: "${PHASE1EF_PREEXECUTION_MANIFEST_VALIDATED:?}"
[[ "$PHASE1EF_PREEXECUTION_MANIFEST_VALIDATED" = YES ]] || exit 65

: "${WORKTREE:?}" "${EXPECTED_COMMIT:?}" "${PYTHON:?}" "${ATTEMPT_ID:?}"
: "${PHASE1EF_ATTEMPT_ID:?}"
: "${PHASE1EF_ATTEMPT_ROOT:?}"
: "${ORIGINAL_AGGREGATE_ROOT:?}" "${SUPPLEMENTAL_AGGREGATE_ROOT:?}"
: "${PRIOR_CAPACITY_PARENT:?}" "${PRIOR_CAPACITY_COMPOSITE:?}" "${PRIOR_PRODUCTION_PACKET:?}"
[[ "$ATTEMPT_ID" = lvef_multitask_phase1ef_post_reallocation_lock_attempt_004 ]]
[[ "$ATTEMPT_ID" = "$PHASE1EF_ATTEMPT_ID" ]]
[[ "$PHASE1EF_ATTEMPT_ROOT" = "/restricted/projectnb/mimicecho/audits/$ATTEMPT_ID" ]]

exec "$PYTHON" "$WORKTREE/scripts/capture_lvef_c3_post_reallocation_capacity.py" \
  --attempt-id "$ATTEMPT_ID" \
  --governing-commit "$EXPECTED_COMMIT" --checkout "$WORKTREE" \
  --attempt-root "$PHASE1EF_ATTEMPT_ROOT" \
  --research-path /restricted/projectnb/mimicecho \
  --backed-path /restricted/project/mimicecho \
  --original-aggregate-root "$ORIGINAL_AGGREGATE_ROOT" \
  --supplemental-aggregate-root "$SUPPLEMENTAL_AGGREGATE_ROOT" \
  --prior-capacity-parent "$PRIOR_CAPACITY_PARENT" \
  --prior-capacity-composite "$PRIOR_CAPACITY_COMPOSITE" \
  --prior-production-packet "$PRIOR_PRODUCTION_PACKET"
