#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run JDIM Tier-1 post-hoc tooling against frozen SCC artifacts.

Required environment:
  JDIM_OUTPUT_ROOT       Restricted root for Tier-1 outputs
  JDIM_PHASE2_ROOT       Frozen stable-v2 result root
  JDIM_SOURCE_STUDIES_CSV  Official release source-study denominator CSV
  JDIM_LINEAGE_JSON      Pinned path-free cohort lineage metadata JSON

Additional for audit-sample/audit-pilot:
  JDIM_AUDIT_KEY_FILE    Restricted file containing at least 16 random bytes

Optional:
  JDIM_FULLSCALE_ROOT, JDIM_LEGACY_ROOT, JDIM_PYTHON_BIN,
  JDIM_BOOTSTRAP_N, JDIM_NONIMAGE_PREDICTIONS

Usage:
  scripts/scc_run_jdim_tier1.sh validate
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
  validate|cohort-flow|reviewer-metrics|audit-sample|audit-pilot) ;;
  *)
    echo "[error] Unknown mode: ${MODE}" >&2
    usage
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

require_env JDIM_OUTPUT_ROOT
require_env JDIM_PHASE2_ROOT
require_env JDIM_SOURCE_STUDIES_CSV
require_env JDIM_LINEAGE_JSON

PY="${JDIM_PYTHON_BIN:-${REPO_ROOT}/.venv-echoprime/bin/python}"
FULLSCALE="${JDIM_FULLSCALE_ROOT:-${REPO_ROOT}/outputs/cloud_cohorts/fullscale_all}"
LEGACY="${JDIM_LEGACY_ROOT:-${REPO_ROOT}/outputs/cloud_cohorts/stage_d_500study_scc}"
OUT="${JDIM_OUTPUT_ROOT}"
PHASE2="${JDIM_PHASE2_ROOT}"
CONFIG="${REPO_ROOT}/configs/jdim_input_content_audit_v1.yaml"
BOOTSTRAP_N="${JDIM_BOOTSTRAP_N:-2000}"

require_absolute_outside_repo "${OUT}" "JDIM_OUTPUT_ROOT"
require_executable "${PY}"
require_file "${JDIM_SOURCE_STUDIES_CSV}"
require_file "${JDIM_LINEAGE_JSON}"
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
require_file "${FULLSCALE}/manifests/all_eligible_studies.csv"
require_file "${FULLSCALE}/study_embeddings_512/study_embedding_manifest.csv"
require_file "${FULLSCALE}/manifests/structured_measurements.csv"
require_file "${FULLSCALE}/manifests/subject_split_map_v1.csv"
require_file "${PHASE2}/lvot_vti/all_clips/imaging_baseline_summary.json"
require_file "${PHASE2}/tapse/all_clips/imaging_baseline_summary.json"
require_file "${PHASE2}/lvot_vti/all_clips/imaging_baseline_predictions.csv"
require_file "${PHASE2}/tapse/all_clips/imaging_baseline_predictions.csv"

EMBEDDING_ARGS=(
  --embedding-batch "legacy_stage_d_500=${LEGACY}/echoprime_embeddings_512/clip_embedding_manifest.csv"
)
for path in "${FULLSCALE_EMBEDDINGS[@]}"; do
  batch_dir="$(basename "$(dirname "${path}")")"
  batch_name="${batch_dir%_embeddings}"
  EMBEDDING_ARGS+=(--embedding-batch "${batch_name}=${path}")
done

COHORT_COMMON=(
  --source-studies-csv "${JDIM_SOURCE_STUDIES_CSV}"
  --eligible-studies-csv "${FULLSCALE}/manifests/all_eligible_studies.csv"
  --expected-records-csv "${EXPECTED_RECORDS[@]}"
  --dicom-audit-csv "${DICOM_AUDITS[@]}"
  --extraction-manifest-csv "${EXTRACTIONS[@]}"
  "${EMBEDDING_ARGS[@]}"
  --final-study-embedding-manifest-csv "${FULLSCALE}/study_embeddings_512/study_embedding_manifest.csv"
  --structured-measurements-csv "${FULLSCALE}/manifests/structured_measurements.csv"
  --subject-split-map-csv "${FULLSCALE}/manifests/subject_split_map_v1.csv"
  --canonical-summary "lvot_vti=${PHASE2}/lvot_vti/all_clips/imaging_baseline_summary.json"
  --canonical-summary "tapse=${PHASE2}/tapse/all_clips/imaging_baseline_summary.json"
  --lineage-metadata-json "${JDIM_LINEAGE_JSON}"
  --output-dir "${OUT}/aggregate_safe/cohort_flow"
)

METRIC_COMMON=(
  --imaging-predictions "lvot_vti=${PHASE2}/lvot_vti/all_clips/imaging_baseline_predictions.csv"
  --imaging-predictions "tapse=${PHASE2}/tapse/all_clips/imaging_baseline_predictions.csv"
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
      --restricted-output-root "${OUT}/restricted/input_content_audit/${AUDIT_SUFFIX}" \
      --safe-output-dir "${OUT}/aggregate_safe/input_content_audit/${AUDIT_SUFFIX}"
    ;;
esac

echo "[done] JDIM Tier-1 mode completed: ${MODE}"
