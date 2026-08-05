#!/usr/bin/env bash
# Metadata-only full-source and read-only storage preflight. No object body GET.
set -euo pipefail
umask 077

: "${LVEF_C3_PREFLIGHT_ENV_FILE:?LVEF_C3_PREFLIGHT_ENV_FILE is required}"
test -f "$LVEF_C3_PREFLIGHT_ENV_FILE"
test -O "$LVEF_C3_PREFLIGHT_ENV_FILE"
test "$(stat -c '%a' "$LVEF_C3_PREFLIGHT_ENV_FILE")" = "600"
# The SCC-only file contains shell-escaped scalar assignments made by the owner.
source "$LVEF_C3_PREFLIGHT_ENV_FILE"

: "${WORKTREE:?}"
: "${EXPECTED_COMMIT:?}"
: "${PYTHON:?}"
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

cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
test -x "$PYTHON"
[[ "$EXPECTED_SELECTED_SOURCE_SHA256" =~ ^[0-9a-f]{64}$ ]]
[[ "$CURRENT_RESEARCH_USAGE_BYTES" =~ ^[0-9]+$ ]]
[[ "$EXPECTED_MIGRATION_WITNESS_SHA256" =~ ^[0-9a-f]{64}$ ]]
test "$(sha256sum "$SELECTED_SOURCE_MANIFEST" | awk '{print $1}')" = "$EXPECTED_SELECTED_SOURCE_SHA256"
test "$(sha256sum "$SELECTED_STUDIES" | awk '{print $1}')" = "$EXPECTED_SELECTED_STUDIES_SHA256"
test "$(sha256sum "$SPLIT_MAP" | awk '{print $1}')" = "$EXPECTED_SPLIT_MAP_SHA256"
test "$(sha256sum "$RESOURCE_POLICY" | awk '{print $1}')" = "$EXPECTED_RESOURCE_POLICY_SHA256"
test "$(sha256sum "$SAFE_EXPORT_POLICY" | awk '{print $1}')" = "$EXPECTED_SAFE_EXPORT_POLICY_SHA256"
test "$(sha256sum "$MIGRATION_WITNESS" | awk '{print $1}')" = "$EXPECTED_MIGRATION_WITNESS_SHA256"

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
  "$PYTHON" scripts/validate_lvef_c3_resource_preflight_outputs.py \
    --stage source --run-root "$RUN_ROOT" \
    --source-manifest "$SELECTED_SOURCE_MANIFEST" \
    --selected-studies "$SELECTED_STUDIES" \
    --split-map "$SPLIT_MAP" >/dev/null
elif [[ "$SOURCE_FINAL_COUNT" -ne 0 ]]; then
  printf '%s\n' 'INCOMPLETE_FINAL_SOURCE_OUTPUT_SET' >&2
  exit 73
else
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
    --resume \
    >"$RUN_ROOT/restricted/logs/source_preflight.stdout.txt" \
    2>"$RUN_ROOT/restricted/logs/source_preflight.stderr.txt"
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
