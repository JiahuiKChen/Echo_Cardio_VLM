#!/usr/bin/env bash
# Future authority-gated C3 cross-batch finalizer. Not executed in Phase 1E-E.
set -euo pipefail
umask 077

[[ "$#" -eq 2 ]] || { printf '%s\n' 'usage: finalizer EXECUTION_ENV_FILE LAUNCH_AUTHORITY' >&2; exit 64; }
ENV_FILE="$1"
LAUNCH_AUTHORITY="$2"
AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
COMMON="$AUTHORITY_WORKTREE/scripts/lvef_c3_production_scheduler_common.sh"
[[ -f "$COMMON" && ! -L "$COMMON" ]] || { printf '%s\n' 'C3_PRODUCTION_FINALIZER_REFUSED=COMMON_HELPER_INVALID' >&2; exit 78; }
# shellcheck disable=SC1090
source "$COMMON"
lvef_c3_load_runtime "$ENV_FILE"
lvef_c3_validate_launch_authority "$LAUNCH_AUTHORITY" "$ENV_FILE"
LAUNCH_AUTHORITY_SHA256="$(sha256sum "$LAUNCH_AUTHORITY" | awk '{print $1}')"
: "${LVEF_C3_FINALIZATION_AUTHORIZATION_ROOT:?}"
: "${LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT:?}"
lvef_c3_require_private_projectnb_directory "$LVEF_C3_FINALIZATION_AUTHORIZATION_ROOT"
FINALIZATION_AUTHORIZATION_RECEIPT="$LVEF_C3_FINALIZATION_AUTHORIZATION_ROOT/all_batches.authorization.json"
lvef_c3_require_private_regular_file "$FINALIZATION_AUTHORIZATION_RECEIPT"
lvef_c3_require_projectnb_path "$LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT"
"$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/lvef_c3_production_stages.py" \
  validate-stage-authorization \
  --authorization-receipt "$FINALIZATION_AUTHORIZATION_RECEIPT" \
  --stage PRESERVATION_FINALIZATION \
  --batch-id all_batches \
  --attempt-id "$LVEF_C3_ATTEMPT_ID" \
  --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
  --orchestration-contract "$LVEF_C3_ORCHESTRATION_CONTRACT" \
  --batch-plan "$LVEF_C3_BATCH_PLAN" \
  --launch-authority-sha256 "$LAUNCH_AUTHORITY_SHA256" >/dev/null
lvef_c3_bind_job_storage finalization all_batches

FINAL_ROOT="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/finalization"
lvef_c3_require_projectnb_path "$FINAL_ROOT"
mkdir -p "$FINAL_ROOT"
FINAL_OUTPUT="$FINAL_ROOT/lvef_c3_production_finalization.summary.json"
[[ ! -e "$FINAL_OUTPUT" && ! -L "$FINAL_OUTPUT" ]] || lvef_c3_die FINAL_OUTPUT_ALREADY_EXISTS
FINAL_ARGS=()
for INDEX in $(seq 0 18); do
  printf -v BATCH_ID 'c3_batch_%03d' "$INDEX"
  RECEIPT="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/batches/$BATCH_ID/preservation/batch_finalization_receipt.restricted.json"
  lvef_c3_require_private_regular_file "$RECEIPT"
  FINAL_ARGS+=(--batch-receipt "$RECEIPT")
done
"$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/finalize_lvef_c3_production.py" \
  "${FINAL_ARGS[@]}" \
  --expected-governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
  --expected-attempt-id "$LVEF_C3_ATTEMPT_ID" \
  --contract "$LVEF_C3_ORCHESTRATION_CONTRACT" \
  --batch-plan "$LVEF_C3_BATCH_PLAN" \
  --environment-receipt "$LVEF_C3_ENVIRONMENT_RECEIPT" \
  --cache-retirement-authorization-root "$LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT" \
  --production-root "$LVEF_C3_PRODUCTION_ROOT" \
  --output "$FINAL_OUTPUT"
printf '%s\n' 'C3_PRODUCTION_FINALIZER=PASS'
printf '%s\n' 'RAW_DICOM_DELETION=NOT_PERFORMED'
printf '%s\n' 'EXTRACTED_CACHE_RETIREMENT_DURING_FINALIZER=NO'
printf '%s\n' 'PRIOR_OWNER_AUTHORIZED_BATCH_CACHE_RETIREMENTS_REVALIDATED=YES'
