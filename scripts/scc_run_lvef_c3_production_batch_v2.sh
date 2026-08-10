#!/usr/bin/env bash
# Future authority-gated C3 batch runner. Not executed or submitted in Phase 1E-E.
set -euo pipefail
umask 077

[[ "$#" -eq 2 ]] || { printf '%s\n' 'usage: runner EXECUTION_ENV_FILE LAUNCH_AUTHORITY' >&2; exit 64; }
ENV_FILE="$1"
LAUNCH_AUTHORITY="$2"
AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
COMMON="$AUTHORITY_WORKTREE/scripts/lvef_c3_production_scheduler_common.sh"
[[ -f "$COMMON" && ! -L "$COMMON" ]] || { printf '%s\n' 'C3_PRODUCTION_SCHEDULER_REFUSED=COMMON_HELPER_INVALID' >&2; exit 78; }
# shellcheck disable=SC1090 -- resolved through the canonical authority-bound worktree.
source "$COMMON"
lvef_c3_load_runtime "$ENV_FILE"
lvef_c3_validate_launch_authority "$LAUNCH_AUTHORITY" "$ENV_FILE"
LAUNCH_AUTHORITY_SHA256="$(sha256sum "$LAUNCH_AUTHORITY" | awk '{print $1}')"

: "${LVEF_C3_SCHEDULER_STAGE:?LVEF_C3_SCHEDULER_STAGE is required}"
case "$LVEF_C3_SCHEDULER_STAGE" in
  FIRST_BATCH_DOWNLOAD)
    : "${SGE_TASK_ID:?SGE_TASK_ID is required}"
    [[ "$SGE_TASK_ID" == 1 ]] || lvef_c3_die FIRST_BATCH_SCOPE_MISMATCH
    BATCH_ID='c3_batch_000'
    (( SGE_TASK_ID == 1 )) || lvef_c3_die FIRST_BATCH_SCOPE_MISMATCH
    STAGE_KEY='download'
    ;;
  REMAINING_BATCH_DOWNLOAD)
    : "${SGE_TASK_ID:?SGE_TASK_ID is required}"
    [[ "$SGE_TASK_ID" =~ ^[0-9]+$ ]] || lvef_c3_die SGE_TASK_ID_INVALID
    (( SGE_TASK_ID >= 2 && SGE_TASK_ID <= 19 )) || lvef_c3_die REMAINING_BATCH_SCOPE_MISMATCH
    printf -v BATCH_ID 'c3_batch_%03d' "$((SGE_TASK_ID - 1))"
    STAGE_KEY='download'
    ;;
  DICOM_EXTRACTION)
    : "${SGE_TASK_ID:?SGE_TASK_ID is required}"
    [[ "$SGE_TASK_ID" =~ ^[0-9]+$ ]] || lvef_c3_die SGE_TASK_ID_INVALID
    (( SGE_TASK_ID >= 1 && SGE_TASK_ID <= 19 )) || lvef_c3_die SGE_TASK_ID_OUT_OF_RANGE
    printf -v BATCH_ID 'c3_batch_%03d' "$((SGE_TASK_ID - 1))"
    STAGE_KEY='dicom_extraction'
    ;;
  ECHOPRIME_EMBEDDING)
    : "${SGE_TASK_ID:?SGE_TASK_ID is required}"
    [[ "$SGE_TASK_ID" =~ ^[0-9]+$ ]] || lvef_c3_die SGE_TASK_ID_INVALID
    (( SGE_TASK_ID >= 1 && SGE_TASK_ID <= 19 )) || lvef_c3_die SGE_TASK_ID_OUT_OF_RANGE
    printf -v BATCH_ID 'c3_batch_%03d' "$((SGE_TASK_ID - 1))"
    STAGE_KEY='echoprime'
    ;;
  BATCH_PRESERVATION)
    : "${SGE_TASK_ID:?SGE_TASK_ID is required}"
    [[ "$SGE_TASK_ID" =~ ^[0-9]+$ ]] || lvef_c3_die SGE_TASK_ID_INVALID
    (( SGE_TASK_ID >= 1 && SGE_TASK_ID <= 19 )) || lvef_c3_die SGE_TASK_ID_OUT_OF_RANGE
    printf -v BATCH_ID 'c3_batch_%03d' "$((SGE_TASK_ID - 1))"
    STAGE_KEY='preservation'
    ;;
  CACHE_RETIREMENT)
    : "${SGE_TASK_ID:?SGE_TASK_ID is required}"
    [[ "$SGE_TASK_ID" =~ ^[0-9]+$ ]] || lvef_c3_die SGE_TASK_ID_INVALID
    (( SGE_TASK_ID >= 1 && SGE_TASK_ID <= 19 )) || lvef_c3_die SGE_TASK_ID_OUT_OF_RANGE
    printf -v BATCH_ID 'c3_batch_%03d' "$((SGE_TASK_ID - 1))"
    STAGE_KEY='cache_retirement'
    ;;
  *) lvef_c3_die SCHEDULER_STAGE_INVALID ;;
esac
lvef_c3_bind_job_storage "$STAGE_KEY" "$BATCH_ID"
lvef_c3_acquire_batch_lock "$STAGE_KEY" "$BATCH_ID"

BATCH_ROOT="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/batches/$BATCH_ID"
CACHE_BATCH_ROOT="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/extracted_cache/$BATCH_ID"
lvef_c3_require_projectnb_path "$BATCH_ROOT"
lvef_c3_require_projectnb_path "$CACHE_BATCH_ROOT"
mkdir -p "$BATCH_ROOT"

run_download_batch() {
    local download_batch_id="$1"
    local input_ledger="$LVEF_C3_BATCH_LEDGER_ROOT/$download_batch_id.initial.json"
    local authorization_receipt="$LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT/$download_batch_id.authorization.json"
    local output_ledger="$BATCH_ROOT/download_resume_ledger.restricted.json"
    : "${LVEF_C3_GCP_BILLING_PROJECT:?}"
    lvef_c3_require_private_regular_file "$authorization_receipt"
    lvef_c3_require_private_regular_file "$input_ledger"
    LVEF_C3_GCP_BILLING_PROJECT="$LVEF_C3_GCP_BILLING_PROJECT" \
    LVEF_C3_CLOUDSDK_CONFIG="$LVEF_C3_CLOUDSDK_CONFIG" \
    LVEF_C3_CLOUDSDK_CONFIG_RECEIPT="$LVEF_C3_CLOUDSDK_CONFIG_RECEIPT" \
    LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256="$LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256" \
      "$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/lvef_c3_orchestration_core.py" \
      download-batch \
      --contract "$LVEF_C3_ORCHESTRATION_CONTRACT" \
      --plan "$LVEF_C3_BATCH_PLAN" \
      --ledger "$input_ledger" \
      --authorization-receipt "$authorization_receipt" \
      --launch-authority-sha256 "$LAUNCH_AUTHORITY_SHA256" \
      --batch-id "$download_batch_id" \
      --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
      --environment-receipt "$LVEF_C3_ENVIRONMENT_RECEIPT" \
      --output-root "$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/raw" \
      --gcloud-binary "$LVEF_C3_GCLOUD_BINARY" \
      --crc32c-python "$LVEF_C3_CRC32C_PYTHON" \
      --crc32c-worker "$LVEF_C3_CRC32C_WORKER" \
      --ledger-output "$output_ledger"
}

case "$LVEF_C3_SCHEDULER_STAGE" in
  FIRST_BATCH_DOWNLOAD)
    : "${LVEF_C3_BATCH_LEDGER_ROOT:?}"
    : "${LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT:?}"
    lvef_c3_require_projectnb_path "$LVEF_C3_BATCH_LEDGER_ROOT"
    lvef_c3_require_projectnb_path "$LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT"
    run_download_batch c3_batch_000
    unset LVEF_C3_GCP_BILLING_PROJECT
    ;;
  REMAINING_BATCH_DOWNLOAD)
    : "${LVEF_C3_BATCH_LEDGER_ROOT:?}"
    : "${LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT:?}"
    lvef_c3_require_projectnb_path "$LVEF_C3_BATCH_LEDGER_ROOT"
    lvef_c3_require_projectnb_path "$LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT"
    printf -v PRIOR_BATCH_ID 'c3_batch_%03d' "$((SGE_TASK_ID - 2))"
    PRIOR_BATCH_ROOT="$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/batches/$PRIOR_BATCH_ID"
    "$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/validate_lvef_c3_prior_batch_finalization.py" \
      --current-batch-id "$BATCH_ID" --attempt-id "$LVEF_C3_ATTEMPT_ID" \
      --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
      --contract "$LVEF_C3_ORCHESTRATION_CONTRACT" --plan "$LVEF_C3_BATCH_PLAN" \
      --environment-receipt "$LVEF_C3_ENVIRONMENT_RECEIPT" \
      --prior-final-receipt "$PRIOR_BATCH_ROOT/preservation/batch_finalization_receipt.restricted.json" \
      --prior-final-ledger "$PRIOR_BATCH_ROOT/final_resume_ledger.restricted.json" \
      --prior-transition-receipt "$PRIOR_BATCH_ROOT/preservation/cache_retirement_finalized.restricted.json" \
      >/dev/null
    run_download_batch "$BATCH_ID"
    unset LVEF_C3_GCP_BILLING_PROJECT
    ;;
  DICOM_EXTRACTION)
    : "${LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT:?}"
    lvef_c3_require_projectnb_path "$LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT"
    EXTRACTION_AUTHORIZATION_RECEIPT="$LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json"
    lvef_c3_require_private_regular_file "$EXTRACTION_AUTHORIZATION_RECEIPT"
    lvef_c3_require_single_active_extraction_cache "$BATCH_ID"
    mkdir -p "$CACHE_BATCH_ROOT"
    "$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/lvef_c3_production_stages.py" \
      run-dicom-extraction \
      --batch-id "$BATCH_ID" --attempt-id "$LVEF_C3_ATTEMPT_ID" \
      --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
      --authority-worktree "$AUTHORITY_WORKTREE" \
      --orchestration-contract "$LVEF_C3_ORCHESTRATION_CONTRACT" \
      --batch-plan "$LVEF_C3_BATCH_PLAN" \
      --environment-receipt "$LVEF_C3_ENVIRONMENT_RECEIPT" \
      --output-root "$CACHE_BATCH_ROOT" \
      --authorization-receipt "$EXTRACTION_AUTHORIZATION_RECEIPT" \
      --launch-authority-sha256 "$LAUNCH_AUTHORITY_SHA256" \
      --verified-download-manifest "$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/raw/$BATCH_ID/verified_download_manifest.restricted.csv" \
      --download-root "$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/raw/$BATCH_ID/objects" \
      --workers "${LVEF_C3_EXTRACTION_WORKERS:-4}" \
      --input-ledger "$BATCH_ROOT/download_resume_ledger.restricted.json" \
      --output-ledger "$BATCH_ROOT/extraction_resume_ledger.restricted.json"
    ;;
  ECHOPRIME_EMBEDDING)
    : "${LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT:?}"
    lvef_c3_require_projectnb_path "$LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT"
    ECHOPRIME_AUTHORIZATION_RECEIPT="$LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json"
    lvef_c3_require_private_regular_file "$ECHOPRIME_AUTHORIZATION_RECEIPT"
    "$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/lvef_c3_production_stages.py" \
      run-echoprime \
      --batch-id "$BATCH_ID" --attempt-id "$LVEF_C3_ATTEMPT_ID" \
      --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
      --authority-worktree "$AUTHORITY_WORKTREE" \
      --orchestration-contract "$LVEF_C3_ORCHESTRATION_CONTRACT" \
      --batch-plan "$LVEF_C3_BATCH_PLAN" --output-root "$BATCH_ROOT" \
      --authorization-receipt "$ECHOPRIME_AUTHORIZATION_RECEIPT" \
      --launch-authority-sha256 "$LAUNCH_AUTHORITY_SHA256" \
      --extraction-manifest "$CACHE_BATCH_ROOT/dicom_extraction/extraction_manifest.restricted.csv" \
      --extraction-root "$CACHE_BATCH_ROOT/dicom_extraction/clips" \
      --selected-batch-manifest "$LVEF_C3_PRODUCTION_ROOT/attempts/$LVEF_C3_ATTEMPT_ID/raw/$BATCH_ID/selected_batch.restricted.csv" \
      --checkpoint "$LVEF_C3_CHECKPOINT" \
      --environment-receipt "$LVEF_C3_ENVIRONMENT_RECEIPT" \
      --batch-size "${LVEF_C3_EMBEDDING_BATCH_SIZE:-8}" --seed 20260803 \
      --input-ledger "$BATCH_ROOT/extraction_resume_ledger.restricted.json" \
      --predecessor-transition-receipt "$CACHE_BATCH_ROOT/dicom_extraction/transition_receipts/extraction_complete.restricted.json" \
      --output-ledger "$BATCH_ROOT/pooling_resume_ledger.restricted.json"
    ;;
  BATCH_PRESERVATION)
    : "${LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT:?}"
    lvef_c3_require_projectnb_path "$LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT"
    PRESERVATION_AUTHORIZATION_RECEIPT="$LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json"
    lvef_c3_require_private_regular_file "$PRESERVATION_AUTHORIZATION_RECEIPT"
    "$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/lvef_c3_production_stages.py" \
      validate-stage-authorization \
      --authorization-receipt "$PRESERVATION_AUTHORIZATION_RECEIPT" \
      --stage BATCH_PRESERVATION --batch-id "$BATCH_ID" \
      --attempt-id "$LVEF_C3_ATTEMPT_ID" \
      --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
      --orchestration-contract "$LVEF_C3_ORCHESTRATION_CONTRACT" \
      --batch-plan "$LVEF_C3_BATCH_PLAN" \
      --launch-authority-sha256 "$LAUNCH_AUTHORITY_SHA256" >/dev/null
    "$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/preserve_lvef_c3_production_batch.py" \
      --contract "$LVEF_C3_ORCHESTRATION_CONTRACT" --plan "$LVEF_C3_BATCH_PLAN" \
      --batch-id "$BATCH_ID" --attempt-id "$LVEF_C3_ATTEMPT_ID" \
      --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
      --production-root "$LVEF_C3_PRODUCTION_ROOT" \
      --output-root "$BATCH_ROOT/preservation" \
      --environment-receipt "$LVEF_C3_ENVIRONMENT_RECEIPT" \
      --checkpoint "$LVEF_C3_CHECKPOINT" \
      --input-ledger "$BATCH_ROOT/pooling_resume_ledger.restricted.json" \
      --scheduler-job-identity "${JOB_ID:?JOB_ID is required}.${SGE_TASK_ID}"
    ;;
  CACHE_RETIREMENT)
    : "${LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT:?}"
    lvef_c3_require_projectnb_path "$LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT"
    CACHE_RETIREMENT_AUTHORIZATION_RECEIPT="$LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT/$BATCH_ID.authorization.json"
    lvef_c3_require_private_regular_file "$CACHE_RETIREMENT_AUTHORIZATION_RECEIPT"
    "$LVEF_C3_PYTHON" "$AUTHORITY_WORKTREE/scripts/retire_lvef_c3_extracted_cache_v2.py" \
      --execute \
      --contract "$LVEF_C3_ORCHESTRATION_CONTRACT" \
      --plan "$LVEF_C3_BATCH_PLAN" \
      --environment-receipt "$LVEF_C3_ENVIRONMENT_RECEIPT" \
      --production-root "$LVEF_C3_PRODUCTION_ROOT" \
      --attempt-id "$LVEF_C3_ATTEMPT_ID" \
      --batch-id "$BATCH_ID" \
      --governing-commit "$LVEF_C3_GOVERNING_COMMIT" \
      --final-ledger "$BATCH_ROOT/cache_retirement_eligible_resume_ledger.restricted.json" \
      --preservation-receipt "$BATCH_ROOT/preservation/batch_preservation_receipt.restricted.json" \
      --authorization-receipt "$CACHE_RETIREMENT_AUTHORIZATION_RECEIPT" \
      --launch-authority-sha256 "$LAUNCH_AUTHORITY_SHA256"
    ;;
esac
printf '%s\n' 'C3_PRODUCTION_BATCH_STAGE=PASS'
printf 'C3_PRODUCTION_BATCH_ID=%s\n' "$BATCH_ID"
printf 'C3_PRODUCTION_STAGE=%s\n' "$LVEF_C3_SCHEDULER_STAGE"
