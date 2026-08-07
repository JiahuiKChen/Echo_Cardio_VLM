#!/usr/bin/env bash
# Metadata-only full-source and read-only storage preflight. No object body GET.
set -euo pipefail
umask 077

test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lvef_c3_billing_environment.sh"
# The broader authority helpers below supersede the prior single-value
# lvef_c3_quarantine_billing_project / lvef_c3_run_with_billing_project path.

: "${LVEF_C3_PREFLIGHT_ENV_FILE:?LVEF_C3_PREFLIGHT_ENV_FILE is required}"
test ! -L "$LVEF_C3_PREFLIGHT_ENV_FILE"
test -f "$LVEF_C3_PREFLIGHT_ENV_FILE"
test -O "$LVEF_C3_PREFLIGHT_ENV_FILE"
test "$(stat -c '%a' "$LVEF_C3_PREFLIGHT_ENV_FILE")" = "600"
# The SCC-only file contains shell-escaped scalar assignments made by the owner.
source "$LVEF_C3_PREFLIGHT_ENV_FILE"
test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"
lvef_c3_quarantine_gcp_authority_environment

: "${WORKTREE:?}"
: "${EXPECTED_COMMIT:?}"
: "${PYTHON:?}"
: "${EXPECTED_PYTHON_SHA256:?}"
: "${SELECTED_STUDIES:?}"
: "${SPLIT_MAP:?}"
: "${SELECTED_SOURCE_MANIFEST:?}"
: "${EXPECTED_SELECTED_SOURCE_SHA256:?}"
: "${RESOURCE_POLICY:?}"
: "${SAFE_EXPORT_POLICY:?}"
: "${EXPECTED_SELECTED_STUDIES_SHA256:?}"
: "${EXPECTED_SPLIT_MAP_SHA256:?}"
: "${EXPECTED_RESOURCE_POLICY_SHA256:?}"
: "${EXPECTED_SAFE_EXPORT_POLICY_SHA256:?}"
: "${CURRENT_RESEARCH_USAGE_BYTES:?}"
: "${MIGRATION_WITNESS:?}"
: "${EXPECTED_MIGRATION_WITNESS_SHA256:?}"
: "${APPROVED_ORGANIZATION_CLASS:?}"
: "${RUN_ROOT:?}"
: "${LVEF_C3_GCP_BILLING_PROJECT:?}"
: "${LVEF_C3_EXPECTED_GCP_ACCOUNT:?}"
: "${LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME:?}"
: "${GCP_AUTHORITY_WRAPPER:?}"
: "${EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256:?}"
: "${C3_EXECUTION_CONTRACT:?}"
: "${EXPECTED_C3_EXECUTION_CONTRACT_SHA256:?}"
: "${GCLOUD_RESOLVER:?}"
: "${EXPECTED_GCLOUD_RESOLVER_SHA256:?}"
: "${GCP_QUOTA_PROJECT_STAGE:?}"
: "${EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256:?}"

GCLOUD="${GCLOUD:-}"
LVEF_C3_GCP_AUTHORIZED_USER_FILE="${LVEF_C3_GCP_AUTHORIZED_USER_FILE:-}"
if [[ -n "$GCLOUD" ]]; then
  test -n "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test -x "$GCLOUD"
  : "${GCLOUD_RESOLUTION_RECORD:?}"
  : "${EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256:?}"
  test "$(sha256sum "$GCLOUD_RESOLUTION_RECORD" | awk '{print $1}')" = "$EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256"
  : "${CLOUDSDK_CONFIG:?}"
  "$WORKTREE/scripts/check_lvef_private_directory.sh" "$CLOUDSDK_CONFIG"
  export CLOUDSDK_CONFIG
  test ! -L "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test -f "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test -O "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test "$(stat -c '%a' "$LVEF_C3_GCP_AUTHORIZED_USER_FILE")" = '600'
else
  test -n "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test ! -L "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test -f "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test -O "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test "$(stat -c '%a' "$LVEF_C3_GCP_AUTHORIZED_USER_FILE")" = '600'
fi
test ! -L "$GCP_QUOTA_PROJECT_STAGE"
test -f "$GCP_QUOTA_PROJECT_STAGE"
test -O "$GCP_QUOTA_PROJECT_STAGE"
test "$(stat -c '%a' "$GCP_QUOTA_PROJECT_STAGE")" = '600'
test "$(sha256sum "$GCP_QUOTA_PROJECT_STAGE" | awk '{print $1}')" = "$EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256"

cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
test -x "$PYTHON"
test "$(sha256sum "$PYTHON" | awk '{print $1}')" = "$EXPECTED_PYTHON_SHA256"
[[ "$EXPECTED_SELECTED_SOURCE_SHA256" =~ ^[0-9a-f]{64}$ ]]
[[ "$CURRENT_RESEARCH_USAGE_BYTES" =~ ^[0-9]+$ ]]
[[ "$EXPECTED_MIGRATION_WITNESS_SHA256" =~ ^[0-9a-f]{64}$ ]]
test "$(sha256sum "$SELECTED_SOURCE_MANIFEST" | awk '{print $1}')" = "$EXPECTED_SELECTED_SOURCE_SHA256"
test "$(sha256sum "$SELECTED_STUDIES" | awk '{print $1}')" = "$EXPECTED_SELECTED_STUDIES_SHA256"
test "$(sha256sum "$SPLIT_MAP" | awk '{print $1}')" = "$EXPECTED_SPLIT_MAP_SHA256"
test "$(sha256sum "$RESOURCE_POLICY" | awk '{print $1}')" = "$EXPECTED_RESOURCE_POLICY_SHA256"
test "$(sha256sum "$SAFE_EXPORT_POLICY" | awk '{print $1}')" = "$EXPECTED_SAFE_EXPORT_POLICY_SHA256"
test "$(sha256sum "$MIGRATION_WITNESS" | awk '{print $1}')" = "$EXPECTED_MIGRATION_WITNESS_SHA256"
test "$(sha256sum "$GCP_AUTHORITY_WRAPPER" | awk '{print $1}')" = "$EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256"
test "$(sha256sum "$C3_EXECUTION_CONTRACT" | awk '{print $1}')" = "$EXPECTED_C3_EXECUTION_CONTRACT_SHA256"
test "$(sha256sum "$GCLOUD_RESOLVER" | awk '{print $1}')" = "$EXPECTED_GCLOUD_RESOLVER_SHA256"

mkdir -p "$RUN_ROOT/aggregate" "$RUN_ROOT/restricted/logs" "$RUN_ROOT/restricted/source_preflight"

DIRECT_PURPOSE='Phase 1E-B/C metadata-only C3 source, storage, and resource preflight'
DIRECT_RECEIPT="$RUN_ROOT/restricted/approved_direct_restricted_agent_receipt.json"
COMMAND_SHA256="$(sha256sum scripts/scc_run_lvef_c3_resource_preflight.sh | awk '{print $1}')"
if [[ -e "$DIRECT_RECEIPT" ]]; then
  "$PYTHON" scripts/lvef_multitask_analysis_modes.py \
    --policy "$SAFE_EXPORT_POLICY" direct-receipt-validate \
    --receipt "$DIRECT_RECEIPT" \
    --source-commit "$EXPECTED_COMMIT" \
    --purpose "$DIRECT_PURPOSE" >/dev/null
else
  "$PYTHON" scripts/lvef_multitask_analysis_modes.py \
    --policy "$SAFE_EXPORT_POLICY" direct-receipt \
    --receipt "$DIRECT_RECEIPT" \
    --purpose "$DIRECT_PURPOSE" \
    --source-commit "$EXPECTED_COMMIT" \
    --organization-class "$APPROVED_ORGANIZATION_CLASS" \
    --command-sha256 "$COMMAND_SHA256" \
    --hash-inputs \
    --input "$SELECTED_STUDIES" \
    --input "$SPLIT_MAP" \
    --input "$SELECTED_SOURCE_MANIFEST" \
    --input "$MIGRATION_WITNESS" \
    --output "$RUN_ROOT/restricted/source_preflight" \
    --output "$RUN_ROOT/aggregate" >/dev/null
fi

GCP_AUTHORITY_RESTRICTED="$RUN_ROOT/restricted/gcp_authority_receipt.restricted.json"
GCP_AUTHORITY_SUMMARY="$RUN_ROOT/aggregate/gcp_authority_receipt.summary.json"
"$GCP_AUTHORITY_WRAPPER" \
  --preflight-env "$LVEF_C3_PREFLIGHT_ENV_FILE" \
  --restricted-output "$GCP_AUTHORITY_RESTRICTED" \
  --aggregate-output "$GCP_AUTHORITY_SUMMARY" \
  >"$RUN_ROOT/restricted/logs/gcp_authority_gate.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/gcp_authority_gate.stderr.txt"
GCP_AUTHORITY_RECEIPT_SHA256="$(sha256sum "$GCP_AUTHORITY_RESTRICTED" | awk '{print $1}')"
[[ "$GCP_AUTHORITY_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ ]]

STORAGE_DETAIL="$RUN_ROOT/restricted/scc_storage_inventory.restricted.json"
STORAGE_SUMMARY="$RUN_ROOT/aggregate/scc_storage_inventory.summary.json"
if [[ -e "$STORAGE_DETAIL" || -e "$STORAGE_SUMMARY" ]]; then
  test -f "$STORAGE_DETAIL"
  test -f "$STORAGE_SUMMARY"
  "$PYTHON" scripts/validate_lvef_c3_resource_preflight_outputs.py \
    --stage storage --run-root "$RUN_ROOT" >/dev/null
else
  "$PYTHON" scripts/audit_lvef_c3_storage.py \
    --resource-policy "$RESOURCE_POLICY" \
    --safe-export-policy "$SAFE_EXPORT_POLICY" \
    --restricted-output "$STORAGE_DETAIL" \
    --aggregate-output "$STORAGE_SUMMARY" \
    >"$RUN_ROOT/restricted/logs/storage_audit.stdout.txt" \
    2>"$RUN_ROOT/restricted/logs/storage_audit.stderr.txt"
fi

SOURCE_FINALS=(
  "$RUN_ROOT/aggregate/c3_full_source_preflight.summary.json"
  "$RUN_ROOT/aggregate/c3_full_source_preflight_by_batch.csv"
  "$RUN_ROOT/aggregate/c3_full_source_cost_estimate.json"
  "$RUN_ROOT/aggregate/c3_full_source_preflight_safety_gate.json"
  "$RUN_ROOT/restricted/source_preflight/c3_full_source_object_metadata.restricted.jsonl"
  "$RUN_ROOT/restricted/source_preflight/c3_full_source_discrepancies.restricted.jsonl"
)
SOURCE_FINAL_COUNT=0
for path in "${SOURCE_FINALS[@]}"; do
  [[ -e "$path" ]] && SOURCE_FINAL_COUNT=$((SOURCE_FINAL_COUNT + 1))
done
if [[ "$SOURCE_FINAL_COUNT" -eq "${#SOURCE_FINALS[@]}" ]]; then
  unset LVEF_C3_GCP_BILLING_PROJECT
  "$PYTHON" scripts/validate_lvef_c3_resource_preflight_outputs.py \
    --stage source --run-root "$RUN_ROOT" \
    --source-manifest "$SELECTED_SOURCE_MANIFEST" \
    --selected-studies "$SELECTED_STUDIES" \
    --split-map "$SPLIT_MAP" >/dev/null
elif [[ "$SOURCE_FINAL_COUNT" -ne 0 ]]; then
  unset LVEF_C3_GCP_BILLING_PROJECT
  printf '%s\n' 'INCOMPLETE_FINAL_SOURCE_OUTPUT_SET' >&2
  exit 73
else
  if lvef_c3_run_with_gcp_authority_environment \
    "$PYTHON" scripts/preflight_lvef_c3_full_source.py \
    --source-manifest "$SELECTED_SOURCE_MANIFEST" \
    --selected-studies "$SELECTED_STUDIES" \
    --split-map "$SPLIT_MAP" \
    --expected-source-manifest-sha256 "$EXPECTED_SELECTED_SOURCE_SHA256" \
    --expected-split-manifest-sha256 "$EXPECTED_SPLIT_MAP_SHA256" \
    --resource-policy "$RESOURCE_POLICY" \
    --safe-export-policy "$SAFE_EXPORT_POLICY" \
    --restricted-output-dir "$RUN_ROOT/restricted/source_preflight" \
    --aggregate-output-dir "$RUN_ROOT/aggregate" \
    --gcloud-bin "$GCLOUD" \
    --gcp-authority-receipt "$GCP_AUTHORITY_RESTRICTED" \
    --expected-gcp-authority-receipt-sha256 "$GCP_AUTHORITY_RECEIPT_SHA256" \
    --resume \
    >"$RUN_ROOT/restricted/logs/source_preflight.stdout.txt" \
    2>"$RUN_ROOT/restricted/logs/source_preflight.stderr.txt"; then
    :
  else
    SOURCE_PREFLIGHT_STATUS=$?
    exit "$SOURCE_PREFLIGHT_STATUS"
  fi
fi

RESOURCE_PLAN="$RUN_ROOT/aggregate/c3_full_resource_plan.json"
if [[ -e "$RESOURCE_PLAN" ]]; then
  "$PYTHON" scripts/validate_lvef_c3_resource_preflight_outputs.py \
    --stage resource --run-root "$RUN_ROOT" \
    --resource-policy "$RESOURCE_POLICY" \
    --safe-export-policy "$SAFE_EXPORT_POLICY" \
    --current-research-usage-bytes "$CURRENT_RESEARCH_USAGE_BYTES" \
    --migration-witness "$MIGRATION_WITNESS" >/dev/null
else
  "$PYTHON" scripts/plan_lvef_c3_resources.py \
    --source-summary "$RUN_ROOT/aggregate/c3_full_source_preflight.summary.json" \
    --batch-table "$RUN_ROOT/aggregate/c3_full_source_preflight_by_batch.csv" \
    --resource-policy "$RESOURCE_POLICY" \
    --safe-export-policy "$SAFE_EXPORT_POLICY" \
    --current-research-usage-bytes "$CURRENT_RESEARCH_USAGE_BYTES" \
    --migration-witness "$MIGRATION_WITNESS" \
    --output "$RESOURCE_PLAN" \
    >"$RUN_ROOT/restricted/logs/resource_plan.stdout.txt" \
    2>"$RUN_ROOT/restricted/logs/resource_plan.stderr.txt"
fi

printf '%s\n' 'lvef_c3_resource_preflight=PASS_METADATA_ONLY'
