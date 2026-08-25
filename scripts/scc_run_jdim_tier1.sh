#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run JDIM Tier-1 post-hoc tooling against frozen SCC artifacts.

Required environment:
  JDIM_REPO_ROOT        Exact isolated Git worktree containing this wrapper
  JDIM_PYTHON_BIN       Exact Python executable
  JDIM_FULLSCALE_ROOT   Canonical full-scale input root
  JDIM_LEGACY_ROOT      Historical legacy Stage-D input root
  JDIM_OUTPUT_ROOT       Restricted root for Tier-1 outputs
  JDIM_PHASE2_ROOT       Frozen stable-v2 result root
  JDIM_SOURCE_STUDIES_CSV  Official release source-study denominator CSV
  JDIM_LINEAGE_JSON      Pinned path-free cohort lineage metadata JSON
  JDIM_SELECTED_STUDIES_CSV  Canonical selected-study universe
  JDIM_STRUCTURED_MEASUREMENTS_CSV  Structured report measurements
  JDIM_SPLIT_MAP_CSV     Frozen subject split map
  JDIM_STUDY_EMBEDDING_NPZ  Study-level embedding array
  JDIM_STUDY_EMBEDDING_MANIFEST_CSV  Study-level embedding manifest
  JDIM_ENCODER_CHECKPOINT  Frozen EchoPrime visual encoder checkpoint
  JDIM_LVOT_SUMMARY_JSON, JDIM_TAPSE_SUMMARY_JSON
  JDIM_LVOT_PREDICTIONS_CSV, JDIM_TAPSE_PREDICTIONS_CSV
  JDIM_AUDIT_CONFIG      Locked input-content audit configuration
  JDIM_RESTRICTED_AUDIT_ROOT  Restricted audit workspace

Additional for audit-sample/audit-pilot:
  JDIM_AUDIT_KEY_FILE    Restricted file containing at least 16 random bytes

Optional:
  JDIM_BOOTSTRAP_N, JDIM_NONIMAGE_PREDICTIONS

Usage:
  scripts/scc_run_jdim_tier1.sh validate
  scripts/scc_run_jdim_tier1.sh handoff-check
  scripts/scc_run_jdim_tier1.sh cohort-flow
  scripts/scc_run_jdim_tier1.sh reviewer-metrics
  scripts/scc_run_jdim_tier1.sh audit-sample
  scripts/scc_run_jdim_tier1.sh audit-pilot
EOF
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "[error] Required environment variable is unset: ${name}" >&2
    exit 2
  fi
}

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "[error] Required file is missing: ${path}" >&2
    exit 2
  fi
}

require_executable() {
  local path="$1"
  if [[ ! -x "${path}" ]]; then
    echo "[error] Required executable is missing or not executable: ${path}" >&2
    exit 2
  fi
}

require_directory() {
  local path="$1"
  if [[ ! -d "${path}" ]]; then
    echo "[error] Required directory is missing: ${path}" >&2
    exit 2
  fi
}

require_absolute_outside_repo() {
  local path="$1"
  local label="$2"
  if [[ "${path}" != /* ]]; then
    echo "[error] ${label} must be an absolute path: ${path}" >&2
    exit 2
  fi
  if [[ "${path}" == "${REPO_ROOT}" || "${path}" == "${REPO_ROOT}/"* ]]; then
    echo "[error] ${label} must remain outside the Git worktree: ${path}" >&2
    exit 2
  fi
}

MODE="${1:-}"
if [[ -z "${MODE}" || "${MODE}" == "-h" || "${MODE}" == "--help" ]]; then
  usage
  exit 0
fi

case "${MODE}" in
  validate|handoff-check|cohort-flow|reviewer-metrics|audit-sample|audit-pilot) ;;
  *)
    echo "[error] Unknown mode: ${MODE}" >&2
    usage
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DERIVED_REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
require_env JDIM_REPO_ROOT
REPO_ROOT="$(cd "${JDIM_REPO_ROOT}" && pwd)"
if [[ "${REPO_ROOT}" != "${DERIVED_REPO_ROOT}" ]]; then
  echo "[error] JDIM_REPO_ROOT does not match the worktree containing this wrapper" >&2
  exit 2
fi
cd "${REPO_ROOT}"

require_env JDIM_PYTHON_BIN
require_env JDIM_FULLSCALE_ROOT
require_env JDIM_LEGACY_ROOT
require_env JDIM_OUTPUT_ROOT
require_env JDIM_PHASE2_ROOT
require_env JDIM_SOURCE_STUDIES_CSV
require_env JDIM_LINEAGE_JSON
require_env JDIM_SELECTED_STUDIES_CSV
require_env JDIM_STRUCTURED_MEASUREMENTS_CSV
require_env JDIM_SPLIT_MAP_CSV
require_env JDIM_STUDY_EMBEDDING_NPZ
require_env JDIM_STUDY_EMBEDDING_MANIFEST_CSV
require_env JDIM_ENCODER_CHECKPOINT
require_env JDIM_LVOT_SUMMARY_JSON
require_env JDIM_TAPSE_SUMMARY_JSON
require_env JDIM_LVOT_PREDICTIONS_CSV
require_env JDIM_TAPSE_PREDICTIONS_CSV
require_env JDIM_AUDIT_CONFIG
require_env JDIM_RESTRICTED_AUDIT_ROOT

PY="${JDIM_PYTHON_BIN}"
FULLSCALE="${JDIM_FULLSCALE_ROOT}"
LEGACY="${JDIM_LEGACY_ROOT}"
OUT="${JDIM_OUTPUT_ROOT}"
PHASE2="${JDIM_PHASE2_ROOT}"
SOURCE_STUDIES="${JDIM_SOURCE_STUDIES_CSV}"
LINEAGE="${JDIM_LINEAGE_JSON}"
SELECTED_STUDIES="${JDIM_SELECTED_STUDIES_CSV}"
STRUCTURED_MEASUREMENTS="${JDIM_STRUCTURED_MEASUREMENTS_CSV}"
SPLIT_MAP="${JDIM_SPLIT_MAP_CSV}"
STUDY_EMBEDDING_NPZ="${JDIM_STUDY_EMBEDDING_NPZ}"
STUDY_EMBEDDING_MANIFEST="${JDIM_STUDY_EMBEDDING_MANIFEST_CSV}"
ENCODER_CHECKPOINT="${JDIM_ENCODER_CHECKPOINT}"
LVOT_SUMMARY="${JDIM_LVOT_SUMMARY_JSON}"
TAPSE_SUMMARY="${JDIM_TAPSE_SUMMARY_JSON}"
LVOT_PREDICTIONS="${JDIM_LVOT_PREDICTIONS_CSV}"
TAPSE_PREDICTIONS="${JDIM_TAPSE_PREDICTIONS_CSV}"
CONFIG="${JDIM_AUDIT_CONFIG}"
RESTRICTED_AUDIT_ROOT="${JDIM_RESTRICTED_AUDIT_ROOT}"
BOOTSTRAP_N="${JDIM_BOOTSTRAP_N:-2000}"

require_absolute_outside_repo "${OUT}" "JDIM_OUTPUT_ROOT"
require_absolute_outside_repo "${RESTRICTED_AUDIT_ROOT}" "JDIM_RESTRICTED_AUDIT_ROOT"
require_executable "${PY}"
require_directory "${FULLSCALE}"
require_directory "${LEGACY}"
require_directory "${PHASE2}"
require_file "${SOURCE_STUDIES}"
require_file "${LINEAGE}"
require_file "${SELECTED_STUDIES}"
require_file "${STRUCTURED_MEASUREMENTS}"
require_file "${SPLIT_MAP}"
require_file "${STUDY_EMBEDDING_NPZ}"
require_file "${STUDY_EMBEDDING_MANIFEST}"
require_file "${ENCODER_CHECKPOINT}"
require_file "${LVOT_SUMMARY}"
require_file "${TAPSE_SUMMARY}"
require_file "${LVOT_PREDICTIONS}"
require_file "${TAPSE_PREDICTIONS}"
require_file "${CONFIG}"

shopt -s nullglob
EXPECTED_RECORDS=("${LEGACY}/manifests/selected_records.csv" "${FULLSCALE}"/batches/batch_*_records.csv)
DICOM_AUDITS=("${LEGACY}/audit/dicom_audit.csv" "${FULLSCALE}"/batches/batch_*_audit/dicom_audit.csv)
EXTRACTIONS=("${LEGACY}/extract_allclip/extraction_manifest.csv" "${FULLSCALE}"/batches/batch_*_extraction_manifest.csv)
FULLSCALE_EMBEDDINGS=("${FULLSCALE}"/batches/batch_*_embeddings/clip_embedding_manifest.csv)

if (( ${#FULLSCALE_EMBEDDINGS[@]} == 0 )); then
  echo "[error] No full-scale embedding batch manifests were discovered under: ${FULLSCALE}/batches" >&2
  exit 2
fi

for path in "${EXPECTED_RECORDS[@]}" "${DICOM_AUDITS[@]}" "${EXTRACTIONS[@]}"; do
  require_file "${path}"
done
require_file "${LEGACY}/echoprime_embeddings_512/clip_embedding_manifest.csv"

EMBEDDING_ARGS=(
  --embedding-batch "legacy_stage_d_500=${LEGACY}/echoprime_embeddings_512/clip_embedding_manifest.csv"
)
for path in "${FULLSCALE_EMBEDDINGS[@]}"; do
  batch_dir="$(basename "$(dirname "${path}")")"
  batch_name="${batch_dir%_embeddings}"
  EMBEDDING_ARGS+=(--embedding-batch "${batch_name}=${path}")
done

COHORT_COMMON=(
  --source-studies-csv "${SOURCE_STUDIES}"
  --eligible-studies-csv "${SELECTED_STUDIES}"
  --expected-records-csv "${EXPECTED_RECORDS[@]}"
  --dicom-audit-csv "${DICOM_AUDITS[@]}"
  --extraction-manifest-csv "${EXTRACTIONS[@]}"
  "${EMBEDDING_ARGS[@]}"
  --final-study-embedding-manifest-csv "${STUDY_EMBEDDING_MANIFEST}"
  --structured-measurements-csv "${STRUCTURED_MEASUREMENTS}"
  --subject-split-map-csv "${SPLIT_MAP}"
  --canonical-summary "lvot_vti=${LVOT_SUMMARY}"
  --canonical-summary "tapse=${TAPSE_SUMMARY}"
  --lineage-metadata-json "${LINEAGE}"
  --output-dir "${OUT}/aggregate_safe/cohort_flow"
)

METRIC_COMMON=(
  --imaging-predictions "lvot_vti=${LVOT_PREDICTIONS}"
  --imaging-predictions "tapse=${TAPSE_PREDICTIONS}"
  --output-dir "${OUT}/aggregate_safe/reviewer_metrics"
  --bootstrap-n "${BOOTSTRAP_N}"
  --bootstrap-seed 20260824
  --restricted-inputs-acknowledged
)

if [[ -n "${JDIM_NONIMAGE_PREDICTIONS:-}" ]]; then
  read -r -a NONIMAGE_ITEMS <<< "${JDIM_NONIMAGE_PREDICTIONS}"
  for item in "${NONIMAGE_ITEMS[@]}"; do
    METRIC_COMMON+=(--nonimage-predictions "${item}")
  done
fi

case "${MODE}" in
  handoff-check)
    printf '%s\n' \
      "[ok] repository/worktree root resolved explicitly" \
      "[ok] Python executable resolved explicitly" \
      "[ok] canonical full-scale and legacy roots resolved explicitly" \
      "[ok] selected universe, measurements, split, embeddings, checkpoint, predictions, and audit roots exist"
    ;;
  validate)
    "${PY}" scripts/reconstruct_jdim_cohort_flow.py "${COHORT_COMMON[@]}" --schema-only
    "${PY}" scripts/compute_jdim_fixed_prediction_metrics.py "${METRIC_COMMON[@]}" --schema-only
    "${PY}" scripts/prepare_jdim_input_audit.py validate-config --config "${CONFIG}"
    if [[ -f "${OUT}/restricted/cohort_flow/jdim_target_cohort_lvot_vti.csv" && \
          -f "${OUT}/restricted/cohort_flow/jdim_target_cohort_tapse.csv" ]]; then
      "${PY}" scripts/prepare_jdim_input_audit.py validate-inputs \
        --config "${CONFIG}" \
        --cohort "lvot_vti=${OUT}/restricted/cohort_flow/jdim_target_cohort_lvot_vti.csv" \
        --cohort "tapse=${OUT}/restricted/cohort_flow/jdim_target_cohort_tapse.csv"
    fi
    ;;
  cohort-flow)
    "${PY}" scripts/reconstruct_jdim_cohort_flow.py \
      "${COHORT_COMMON[@]}" \
      --restricted-reconciliation-csv "${OUT}/restricted/cohort_flow/reconciliation.csv"
    ;;
  reviewer-metrics)
    "${PY}" scripts/compute_jdim_fixed_prediction_metrics.py "${METRIC_COMMON[@]}"
    ;;
  audit-sample|audit-pilot)
    require_env JDIM_AUDIT_KEY_FILE
    require_absolute_outside_repo "${JDIM_AUDIT_KEY_FILE}" "JDIM_AUDIT_KEY_FILE"
    require_file "${JDIM_AUDIT_KEY_FILE}"
    AUDIT_COMMAND="sample"
    AUDIT_SUFFIX="main"
    if [[ "${MODE}" == "audit-pilot" ]]; then
      AUDIT_COMMAND="pilot"
      AUDIT_SUFFIX="technical_pilot"
    fi
    "${PY}" scripts/prepare_jdim_input_audit.py "${AUDIT_COMMAND}" \
      --config "${CONFIG}" \
      --cohort "lvot_vti=${OUT}/restricted/cohort_flow/jdim_target_cohort_lvot_vti.csv" \
      --cohort "tapse=${OUT}/restricted/cohort_flow/jdim_target_cohort_tapse.csv" \
      --opaque-id-key-file "${JDIM_AUDIT_KEY_FILE}" \
      --restricted-output-root "${RESTRICTED_AUDIT_ROOT}/${AUDIT_SUFFIX}" \
      --safe-output-dir "${OUT}/aggregate_safe/input_content_audit/${AUDIT_SUFFIX}"
    ;;
esac

echo "[done] JDIM Tier-1 mode completed: ${MODE}"
