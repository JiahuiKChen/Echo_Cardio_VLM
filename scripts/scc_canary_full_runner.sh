#!/usr/bin/env bash
# Full end-to-end canary runner for scc4.
# Covers: path verification, workspace prep, canary 2-study run,
#         output collection, and failure diagnostics.
#
# IMPORTANT: This script is for the 2-study canary ONLY.
# The canary is small enough to run interactively on the login node.
# For Stage D (500+ studies) or any GPU work, use the qsub batch
# submission scripts instead:
#   ./scripts/scc_submit_stage_d_job.sh
#   ./scripts/scc_submit_echoprime_embedding_job.sh
# The login node kills interactive processes exceeding 15 min CPU time.
#
# Usage (on scc4):
#   chmod +x scripts/scc_canary_full_runner.sh
#   ./scripts/scc_canary_full_runner.sh
#
# Prereqs on scc4:
#   module load python3/3.10.12
#   module load google-cloud-sdk/455.0.0
#   gcloud auth login  (if not already authenticated)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BILLING_PROJECT="${ECHO_AI_BILLING_PROJECT:-mimic-iv-anesthesia}"
PROJECT_NAME="mimicecho"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="canary_${TIMESTAMP}"
DIAGNOSTICS_DIR="${REPO_ROOT}/outputs/diagnostics/${RUN_ID}"
mkdir -p "${DIAGNOSTICS_DIR}"

LOG_FILE="${DIAGNOSTICS_DIR}/runner.log"
exec > >(tee -a "${LOG_FILE}") 2>&1

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

record_step() {
  local step="$1" status="$2" detail="${3:-}"
  echo "${TIMESTAMP},${step},${status},${detail}" >> "${DIAGNOSTICS_DIR}/steps.csv"
}

echo "step_timestamp,step_name,status,detail" > "${DIAGNOSTICS_DIR}/steps.csv"

fail_report() {
  local step="$1" cmd="$2" rc="$3"
  record_step "${step}" "FAIL" "exit_code=${rc}"
  cat > "${DIAGNOSTICS_DIR}/failure_report.md" <<FAILEOF
# Canary Failure Report

**Run ID:** ${RUN_ID}
**Timestamp:** ${TIMESTAMP}
**Failing step:** ${step}
**Failing command:** \`${cmd}\`
**Exit code:** ${rc}

## stderr/stdout
See \`${LOG_FILE}\` for full output.

## Root-cause hypothesis
$(case "${step}" in
  verify_host)     echo "Not running on scc4. Restricted paths require scc4 login node." ;;
  verify_paths)    echo "Restricted storage not yet provisioned or group ACL missing. Contact SCC support." ;;
  verify_quota)    echo "df could not read quota. Path may not be mounted." ;;
  module_load)     echo "Required module not available. Check \`module avail\` output." ;;
  gcloud_auth)     echo "gcloud auth not configured. Run: gcloud auth login" ;;
  workspace_prep)  echo "scc_prepare_workspace.sh failed. Check path permissions and Python availability." ;;
  source_env)      echo "scc_env.sh was not created by workspace prep. Re-run workspace prep." ;;
  canary_run)      echo "Canary pipeline failed. Check BigQuery access, GCS bucket perms, or Python deps." ;;
  *)               echo "Unknown step failure." ;;
esac)

## Minimum fix & retry
$(case "${step}" in
  verify_host)     echo "SSH to scc4: \`ssh scc4\` from another SCC node, or \`ssh pkarim@scc4.bu.edu\`" ;;
  verify_paths)    echo "Contact SCC: request writable /restricted/project/${PROJECT_NAME} and /restricted/projectnb/${PROJECT_NAME}" ;;
  verify_quota)    echo "Retry after SCC confirms path provisioning." ;;
  module_load)     echo "\`module avail python3\` and \`module avail google-cloud-sdk\` to find correct versions." ;;
  gcloud_auth)     echo "\`gcloud auth login\` then retry." ;;
  workspace_prep)  echo "Fix the error above, then: \`./scripts/scc_prepare_workspace.sh --project-name ${PROJECT_NAME} --billing-project ${BILLING_PROJECT} --setup-env true --run-preflight false\`" ;;
  source_env)      echo "Re-run workspace prep first." ;;
  canary_run)      echo "Fix the error above, then: \`source scc_env.sh && ./scripts/scc_run_canary_2study.sh --billing-project ${BILLING_PROJECT}\`" ;;
  *)               echo "Inspect the log file and retry the failing step." ;;
esac)

## Full log
\`${LOG_FILE}\`

## Output paths
\`${DIAGNOSTICS_DIR}/\`
FAILEOF
  log "[FAIL] Diagnostics written to ${DIAGNOSTICS_DIR}/failure_report.md"
  cat "${DIAGNOSTICS_DIR}/failure_report.md"
  exit "${rc}"
}

# ── Step 1: Verify host ──────────────────────────────────────────────
log "=== Step 1: Host verification ==="
CURRENT_HOST="$(hostname)"
log "hostname: ${CURRENT_HOST}"
log "user: $(whoami)"
log "groups: $(id -Gn)"
echo "${CURRENT_HOST}" > "${DIAGNOSTICS_DIR}/hostname.txt"
id -Gn > "${DIAGNOSTICS_DIR}/groups.txt"

if [[ "${CURRENT_HOST}" != scc4* ]]; then
  log "[WARN] Not on scc4 (current: ${CURRENT_HOST})."
  log "[WARN] Restricted paths may not be visible. Proceeding to check anyway."
  record_step "verify_host" "WARN" "host=${CURRENT_HOST}"
else
  log "[OK] Running on scc4."
  record_step "verify_host" "OK" "host=${CURRENT_HOST}"
fi

# ── Step 2: Verify restricted paths ──────────────────────────────────
log "=== Step 2: Path verification ==="
PATHS_OK=true
for p in "/restricted/project/${PROJECT_NAME}" "/restricted/projectnb/${PROJECT_NAME}"; do
  if ls -ld "${p}" >> "${DIAGNOSTICS_DIR}/path_check.txt" 2>&1; then
    log "[OK] ${p} exists"
    record_step "verify_path_$(basename "$(dirname "${p}")")" "OK" "${p}"
  else
    log "[FAIL] ${p} not accessible"
    record_step "verify_path_$(basename "$(dirname "${p}")")" "FAIL" "${p}"
    PATHS_OK=false
  fi
done

if [[ "${PATHS_OK}" != "true" ]]; then
  fail_report "verify_paths" "ls -ld /restricted/project/${PROJECT_NAME} /restricted/projectnb/${PROJECT_NAME}" 1
fi

# ── Step 3: Verify quota ─────────────────────────────────────────────
log "=== Step 3: Quota/disk verification ==="
log "  Confirmed quotas: /restricted/project = 200 GB, /restricted/projectnb = 800 GB (1 TB total)"
for p in "/restricted/project/${PROJECT_NAME}" "/restricted/projectnb/${PROJECT_NAME}"; do
  if df -h "${p}" >> "${DIAGNOSTICS_DIR}/quota.txt" 2>&1; then
    log "[OK] df succeeded for ${p}"
    AVAIL_KB="$(df -Pk "${p}" 2>/dev/null | awk 'NR==2 {print $4}')"
    AVAIL_GB=$(( ${AVAIL_KB:-0} / 1024 / 1024 ))
    USED_KB="$(df -Pk "${p}" 2>/dev/null | awk 'NR==2 {print $3}')"
    USED_GB=$(( ${USED_KB:-0} / 1024 / 1024 ))
    log "  ${p}: used=${USED_GB} GB, available=${AVAIL_GB} GB"
    record_step "verify_quota_$(basename "$(dirname "${p}")")" "OK" "used=${USED_GB}GB avail=${AVAIL_GB}GB"
  else
    log "[WARN] df failed for ${p}"
    record_step "verify_quota_$(basename "$(dirname "${p}")")" "WARN" "df failed"
  fi
done

# ── Step 4: Module loads ─────────────────────────────────────────────
log "=== Step 4: Module loads ==="
if command -v module >/dev/null 2>&1; then
  module load python3/3.10.12 2>&1 && {
    log "[OK] python3/3.10.12 loaded"
    record_step "module_python" "OK" "$(python3 --version 2>&1)"
  } || {
    log "[WARN] python3/3.10.12 not available, trying system python3"
    record_step "module_python" "WARN" "fallback to system python3"
  }
  module load google-cloud-sdk/455.0.0 2>&1 && {
    log "[OK] google-cloud-sdk/455.0.0 loaded"
    record_step "module_gcloud" "OK" "$(gcloud --version 2>&1 | head -1)"
  } || {
    log "[WARN] google-cloud-sdk/455.0.0 not available"
    record_step "module_gcloud" "WARN" "module load failed"
  }
else
  log "[INFO] module command not available (not on SCC?)"
  record_step "module_load" "SKIP" "module command not found"
fi

# ── Step 5: gcloud auth check ────────────────────────────────────────
log "=== Step 5: gcloud auth check ==="
if command -v gcloud >/dev/null 2>&1; then
  GCLOUD_ACCOUNT="$(gcloud config get-value account 2>/dev/null || echo "")"
  if [[ -n "${GCLOUD_ACCOUNT}" && "${GCLOUD_ACCOUNT}" != "(unset)" ]]; then
    log "[OK] gcloud account: ${GCLOUD_ACCOUNT}"
    record_step "gcloud_auth" "OK" "account=${GCLOUD_ACCOUNT}"
  else
    log "[FAIL] gcloud not authenticated"
    fail_report "gcloud_auth" "gcloud config get-value account" 1
  fi
else
  log "[FAIL] gcloud not in PATH"
  fail_report "gcloud_auth" "command -v gcloud" 1
fi

# ── Step 6: Workspace preparation ────────────────────────────────────
log "=== Step 6: Workspace preparation ==="
cd "${REPO_ROOT}"
if ./scripts/scc_prepare_workspace.sh \
    --project-name "${PROJECT_NAME}" \
    --billing-project "${BILLING_PROJECT}" \
    --setup-env true \
    --run-preflight false 2>&1; then
  log "[OK] Workspace preparation complete"
  record_step "workspace_prep" "OK" ""
else
  fail_report "workspace_prep" \
    "./scripts/scc_prepare_workspace.sh --project-name ${PROJECT_NAME} --billing-project ${BILLING_PROJECT} --setup-env true --run-preflight false" $?
fi

# ── Step 7: Source scc_env.sh ─────────────────────────────────────────
log "=== Step 7: Source scc_env.sh ==="
if [[ -f "${REPO_ROOT}/scc_env.sh" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/scc_env.sh"
  log "[OK] scc_env.sh sourced"
  log "  ECHO_AI_PROJECT_NAME=${ECHO_AI_PROJECT_NAME:-<unset>}"
  log "  ECHO_AI_RESTRICTED_PROJECT=${ECHO_AI_RESTRICTED_PROJECT:-<unset>}"
  log "  ECHO_AI_RESTRICTED_PROJECTNB=${ECHO_AI_RESTRICTED_PROJECTNB:-<unset>}"
  log "  ECHO_AI_DATA_ROOT=${ECHO_AI_DATA_ROOT:-<unset>}"
  log "  ECHO_AI_BILLING_PROJECT=${ECHO_AI_BILLING_PROJECT:-<unset>}"
  record_step "source_env" "OK" "all vars set"
  cp "${REPO_ROOT}/scc_env.sh" "${DIAGNOSTICS_DIR}/scc_env.sh.snapshot"
else
  fail_report "source_env" "source scc_env.sh" 1
fi

# ── Step 7b: Pre-run storage snapshot ─────────────────────────────────
log "=== Step 7b: Storage snapshot (pre-canary) ==="
log "Storage layout: DICOMs → projectnb (800 GB transient), encoded → project (200 GB backed-up)"
log "Strategy: purge raw DICOMs after checksum + manifest + encode"
df -h "/restricted/project/${PROJECT_NAME}" "/restricted/projectnb/${PROJECT_NAME}" 2>&1 | tee -a "${DIAGNOSTICS_DIR}/storage_pre.txt"

# ── Step 8: Canary 2-study run ────────────────────────────────────────
log "=== Step 8: Canary 2-study run ==="
CANARY_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ./scripts/scc_run_canary_2study.sh --billing-project "${BILLING_PROJECT}" 2>&1; then
  CANARY_END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log "[OK] Canary run complete (${CANARY_START} -> ${CANARY_END})"
  record_step "canary_run" "OK" "start=${CANARY_START} end=${CANARY_END}"
else
  CANARY_END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  fail_report "canary_run" \
    "./scripts/scc_run_canary_2study.sh --billing-project ${BILLING_PROJECT}" $?
fi

# ── Step 8b: Post-run storage snapshot ────────────────────────────────
log "=== Step 8b: Storage snapshot (post-canary) ==="
df -h "/restricted/project/${PROJECT_NAME}" "/restricted/projectnb/${PROJECT_NAME}" 2>&1 | tee -a "${DIAGNOSTICS_DIR}/storage_post.txt"

# ── Step 9: Collect and summarize outputs ─────────────────────────────
log "=== Step 9: Output collection ==="
CANARY_COHORT_ROOT="${REPO_ROOT}/outputs/cloud_cohorts/scc_canary_2study"
CANARY_DATA_ROOT="${ECHO_AI_DATA_ROOT}/cloud_cohorts/scc_canary_2study"
SUMMARY_FILE="${DIAGNOSTICS_DIR}/canary_summary.md"

collect_file() {
  local label="$1" path="$2"
  if [[ -f "${path}" ]]; then
    log "[OK] ${label}: ${path}"
    cp "${path}" "${DIAGNOSTICS_DIR}/$(basename "${path}")" 2>/dev/null || true
    return 0
  else
    log "[MISS] ${label}: ${path} not found"
    return 1
  fi
}

cat > "${SUMMARY_FILE}" <<SUMEOF
# Canary 2-Study Run Summary

**Run ID:** ${RUN_ID}
**Started:** ${CANARY_START}
**Completed:** ${CANARY_END}
**Host:** ${CURRENT_HOST}
**Billing project:** ${BILLING_PROJECT}

## Paths Used
- Project storage: ${ECHO_AI_RESTRICTED_PROJECT:-N/A}
- Projectnb storage: ${ECHO_AI_RESTRICTED_PROJECTNB:-N/A}
- Data root: ${ECHO_AI_DATA_ROOT:-N/A}
- Cohort root: ${CANARY_COHORT_ROOT}
- Download root: ${CANARY_DATA_ROOT}

## Selection Summary
SUMEOF

if collect_file "selection_summary" "${CANARY_COHORT_ROOT}/manifests/selection_summary.json"; then
  echo '```json' >> "${SUMMARY_FILE}"
  cat "${CANARY_COHORT_ROOT}/manifests/selection_summary.json" >> "${SUMMARY_FILE}"
  echo '```' >> "${SUMMARY_FILE}"
else
  echo "(not found)" >> "${SUMMARY_FILE}"
fi

echo "" >> "${SUMMARY_FILE}"
echo "## Extraction Manifest Summary" >> "${SUMMARY_FILE}"
if collect_file "extraction_manifest" "${CANARY_COHORT_ROOT}/extract_smoke/extraction_manifest.csv"; then
  EXTRACT_ROWS="$(tail -n +2 "${CANARY_COHORT_ROOT}/extract_smoke/extraction_manifest.csv" | wc -l | tr -d ' ')"
  echo "- Extracted clips: ${EXTRACT_ROWS}" >> "${SUMMARY_FILE}"
  echo '```' >> "${SUMMARY_FILE}"
  head -5 "${CANARY_COHORT_ROOT}/extract_smoke/extraction_manifest.csv" >> "${SUMMARY_FILE}"
  echo '...' >> "${SUMMARY_FILE}"
  echo '```' >> "${SUMMARY_FILE}"
else
  echo "(not found)" >> "${SUMMARY_FILE}"
fi

echo "" >> "${SUMMARY_FILE}"
echo "## Structured Measurements Summary" >> "${SUMMARY_FILE}"
if collect_file "structured_measurements" "${CANARY_COHORT_ROOT}/manifests/structured_measurements.csv"; then
  MEAS_ROWS="$(tail -n +2 "${CANARY_COHORT_ROOT}/manifests/structured_measurements.csv" | wc -l | tr -d ' ')"
  echo "- Measurement rows: ${MEAS_ROWS}" >> "${SUMMARY_FILE}"
  echo '```' >> "${SUMMARY_FILE}"
  head -3 "${CANARY_COHORT_ROOT}/manifests/structured_measurements.csv" >> "${SUMMARY_FILE}"
  echo '...' >> "${SUMMARY_FILE}"
  echo '```' >> "${SUMMARY_FILE}"
else
  echo "(not found)" >> "${SUMMARY_FILE}"
fi

echo "" >> "${SUMMARY_FILE}"
echo "## Keyframe Manifest" >> "${SUMMARY_FILE}"
collect_file "keyframe_manifest" "${CANARY_COHORT_ROOT}/extract_smoke/keyframe_manifest.csv" && {
  KF_ROWS="$(tail -n +2 "${CANARY_COHORT_ROOT}/extract_smoke/keyframe_manifest.csv" | wc -l | tr -d ' ')"
  echo "- Keyframe rows: ${KF_ROWS}" >> "${SUMMARY_FILE}"
} || echo "(not found)" >> "${SUMMARY_FILE}"

echo "" >> "${SUMMARY_FILE}"
echo "## LVEF Still Manifest" >> "${SUMMARY_FILE}"
collect_file "lvef_manifest" "${CANARY_COHORT_ROOT}/manifests/lvef_still_manifest.csv" && {
  LVEF_ROWS="$(tail -n +2 "${CANARY_COHORT_ROOT}/manifests/lvef_still_manifest.csv" | wc -l | tr -d ' ')"
  echo "- LVEF manifest rows: ${LVEF_ROWS}" >> "${SUMMARY_FILE}"
} || echo "(not found)" >> "${SUMMARY_FILE}"

echo "" >> "${SUMMARY_FILE}"
echo "## Subject Split Map" >> "${SUMMARY_FILE}"
collect_file "subject_split_map" "${CANARY_COHORT_ROOT}/manifests/subject_split_map_v1.csv" || echo "(not found)" >> "${SUMMARY_FILE}"

echo "" >> "${SUMMARY_FILE}"
echo "## Download Report" >> "${SUMMARY_FILE}"
collect_file "download_report" "${CANARY_COHORT_ROOT}/manifests/download_report.csv" && {
  echo '```' >> "${SUMMARY_FILE}"
  cat "${CANARY_COHORT_ROOT}/manifests/download_report.csv" >> "${SUMMARY_FILE}"
  echo '```' >> "${SUMMARY_FILE}"
} || echo "(not found)" >> "${SUMMARY_FILE}"

echo "" >> "${SUMMARY_FILE}"
echo "## Audit Artifacts" >> "${SUMMARY_FILE}"
if [[ -d "${CANARY_COHORT_ROOT}/audit" ]]; then
  ls -la "${CANARY_COHORT_ROOT}/audit/" >> "${SUMMARY_FILE}" 2>&1
else
  echo "(audit directory not found)" >> "${SUMMARY_FILE}"
fi

echo "" >> "${SUMMARY_FILE}"
echo "## Storage Usage (pre/post canary)" >> "${SUMMARY_FILE}"
echo '```' >> "${SUMMARY_FILE}"
echo "--- PRE ---" >> "${SUMMARY_FILE}"
cat "${DIAGNOSTICS_DIR}/storage_pre.txt" >> "${SUMMARY_FILE}" 2>/dev/null || echo "(not captured)" >> "${SUMMARY_FILE}"
echo "" >> "${SUMMARY_FILE}"
echo "--- POST ---" >> "${SUMMARY_FILE}"
cat "${DIAGNOSTICS_DIR}/storage_post.txt" >> "${SUMMARY_FILE}" 2>/dev/null || echo "(not captured)" >> "${SUMMARY_FILE}"
echo '```' >> "${SUMMARY_FILE}"

echo "" >> "${SUMMARY_FILE}"
echo "## Reproducibility" >> "${SUMMARY_FILE}"
cat >> "${SUMMARY_FILE}" <<REPEOF
- Full log: \`${LOG_FILE}\`
- Step status CSV: \`${DIAGNOSTICS_DIR}/steps.csv\`
- scc_env.sh snapshot: \`${DIAGNOSTICS_DIR}/scc_env.sh.snapshot\`
- All artifacts copied to: \`${DIAGNOSTICS_DIR}/\`
REPEOF

record_step "output_collection" "OK" "summary at ${SUMMARY_FILE}"

log ""
log "============================================"
log "  CANARY RUN COMPLETE - ALL STEPS PASSED"
log "============================================"
log "Summary: ${SUMMARY_FILE}"
log "Diagnostics: ${DIAGNOSTICS_DIR}/"
log ""
cat "${SUMMARY_FILE}"
