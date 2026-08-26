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
  JDIM_CLIP_EMBEDDING_NPZ   Historically merged clip-level embedding array
  JDIM_CLIP_EMBEDDING_MANIFEST_CSV  Historically merged clip manifest
  JDIM_ENCODER_CHECKPOINT  Frozen EchoPrime visual encoder checkpoint
  JDIM_LVOT_SUMMARY_JSON, JDIM_TAPSE_SUMMARY_JSON
  JDIM_LVOT_PREDICTIONS_CSV, JDIM_TAPSE_PREDICTIONS_CSV
  JDIM_AUDIT_CONFIG      Locked input-content audit configuration
  JDIM_RESTRICTED_AUDIT_ROOT  Restricted audit workspace
  JDIM_CORRECTED_ROOT     New immutable root for duplicate-corrected artifacts

Additional for audit-sample/audit-pilot:
  JDIM_AUDIT_KEY_FILE    Restricted file containing at least 16 random bytes
Additional for audit-reconstruct-pilot:
  JDIM_DICOM_DATA_ROOT   Approved local MIMIC-IV-ECHO DICOM root

Optional:
  JDIM_BOOTSTRAP_N, JDIM_NONIMAGE_PREDICTIONS
  JDIM_PHASE2_RANDOM_SEED, JDIM_PHASE2_RIDGE_ALPHAS
  JDIM_DUPLICATE_DECISION_ROOT  Separate resolved-decision root; defaults to JDIM_OUTPUT_ROOT

Usage:
  scripts/scc_run_jdim_tier1.sh validate
  scripts/scc_run_jdim_tier1.sh handoff-check
  scripts/scc_run_jdim_tier1.sh cohort-flow
  scripts/scc_run_jdim_tier1.sh reviewer-metrics
  scripts/scc_run_jdim_tier1.sh duplicate-forensics
  scripts/scc_run_jdim_tier1.sh corrected-aggregation
  scripts/scc_run_jdim_tier1.sh corrected-analysis
  scripts/scc_run_jdim_tier1.sh corrected-comparison
  scripts/scc_run_jdim_tier1.sh audit-sample
  scripts/scc_run_jdim_tier1.sh audit-pilot
  scripts/scc_run_jdim_tier1.sh audit-reconstruct-pilot
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
  validate|handoff-check|cohort-flow|reviewer-metrics|duplicate-forensics|corrected-aggregation|corrected-analysis|corrected-comparison|audit-sample|audit-pilot|audit-reconstruct-pilot) ;;
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
require_env JDIM_CLIP_EMBEDDING_NPZ
require_env JDIM_CLIP_EMBEDDING_MANIFEST_CSV
require_env JDIM_ENCODER_CHECKPOINT
require_env JDIM_LVOT_SUMMARY_JSON
require_env JDIM_TAPSE_SUMMARY_JSON
require_env JDIM_LVOT_PREDICTIONS_CSV
require_env JDIM_TAPSE_PREDICTIONS_CSV
require_env JDIM_AUDIT_CONFIG
require_env JDIM_RESTRICTED_AUDIT_ROOT
require_env JDIM_CORRECTED_ROOT

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
CLIP_EMBEDDING_NPZ="${JDIM_CLIP_EMBEDDING_NPZ}"
CLIP_EMBEDDING_MANIFEST="${JDIM_CLIP_EMBEDDING_MANIFEST_CSV}"
ENCODER_CHECKPOINT="${JDIM_ENCODER_CHECKPOINT}"
LVOT_SUMMARY="${JDIM_LVOT_SUMMARY_JSON}"
TAPSE_SUMMARY="${JDIM_TAPSE_SUMMARY_JSON}"
LVOT_PREDICTIONS="${JDIM_LVOT_PREDICTIONS_CSV}"
TAPSE_PREDICTIONS="${JDIM_TAPSE_PREDICTIONS_CSV}"
CONFIG="${JDIM_AUDIT_CONFIG}"
RESTRICTED_AUDIT_ROOT="${JDIM_RESTRICTED_AUDIT_ROOT}"
CORRECTED_ROOT="${JDIM_CORRECTED_ROOT}"
DECISION_ROOT="${JDIM_DUPLICATE_DECISION_ROOT:-${OUT}}"
if [[ -n "${JDIM_DUPLICATE_DECISION_ROOT:-}" ]]; then
  DECISION_ROWS="${DECISION_ROOT}/restricted/duplicate_forensics_rows.csv"
  DECISION_SUMMARY="${DECISION_ROOT}/aggregate_safe/duplicate_forensics_summary.json"
else
  DECISION_ROWS="${OUT}/restricted/duplicate_forensics/duplicate_forensics_rows.csv"
  DECISION_SUMMARY="${OUT}/aggregate_safe/duplicate_forensics/duplicate_forensics_summary.json"
fi
BOOTSTRAP_N="${JDIM_BOOTSTRAP_N:-2000}"
PHASE2_RANDOM_SEED="${JDIM_PHASE2_RANDOM_SEED:-1337}"
PHASE2_RIDGE_ALPHAS="${JDIM_PHASE2_RIDGE_ALPHAS:-0.01,0.03,0.1,0.3,1,3,10,30,100,300,1000}"

require_absolute_outside_repo "${OUT}" "JDIM_OUTPUT_ROOT"
require_absolute_outside_repo "${RESTRICTED_AUDIT_ROOT}" "JDIM_RESTRICTED_AUDIT_ROOT"
require_absolute_outside_repo "${CORRECTED_ROOT}" "JDIM_CORRECTED_ROOT"
require_absolute_outside_repo "${DECISION_ROOT}" "JDIM_DUPLICATE_DECISION_ROOT"
if [[ "${CORRECTED_ROOT}" == "${OUT}" || "${CORRECTED_ROOT}" == "${OUT}/"* || "${OUT}" == "${CORRECTED_ROOT}/"* ]]; then
  echo "[error] JDIM_CORRECTED_ROOT and JDIM_OUTPUT_ROOT must be separate immutable roots" >&2
  exit 2
fi
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
require_file "${CLIP_EMBEDDING_NPZ}"
require_file "${CLIP_EMBEDDING_MANIFEST}"
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
EMBEDDING_NPZ_ARGS=(
  --embedding-batch-npz "legacy_stage_d_500=${LEGACY}/echoprime_embeddings_512/clip_embeddings_512.npz"
)
FORENSIC_ARGS=(
  --extraction-manifest "legacy_stage_d_500=${LEGACY}/extract_allclip/extraction_manifest.csv"
  --embedding-manifest "legacy_stage_d_500=${LEGACY}/echoprime_embeddings_512/clip_embedding_manifest.csv"
  --embedding-npz "legacy_stage_d_500=${LEGACY}/echoprime_embeddings_512/clip_embeddings_512.npz"
)
require_file "${LEGACY}/echoprime_embeddings_512/clip_embeddings_512.npz"
for path in "${FULLSCALE_EMBEDDINGS[@]}"; do
  batch_dir="$(basename "$(dirname "${path}")")"
  batch_name="${batch_dir%_embeddings}"
  EMBEDDING_ARGS+=(--embedding-batch "${batch_name}=${path}")
  batch_extraction="${FULLSCALE}/batches/${batch_name}_extraction_manifest.csv"
  batch_npz="$(dirname "${path}")/clip_embeddings_512.npz"
  require_file "${batch_extraction}"
  require_file "${batch_npz}"
  FORENSIC_ARGS+=(
    --extraction-manifest "${batch_name}=${batch_extraction}"
    --embedding-manifest "${batch_name}=${path}"
    --embedding-npz "${batch_name}=${batch_npz}"
  )
  EMBEDDING_NPZ_ARGS+=(--embedding-batch-npz "${batch_name}=${batch_npz}")
done

COHORT_COMMON=(
  --source-studies-csv "${SOURCE_STUDIES}"
  --eligible-studies-csv "${SELECTED_STUDIES}"
  --expected-records-csv "${EXPECTED_RECORDS[@]}"
  --dicom-audit-csv "${DICOM_AUDITS[@]}"
  --extraction-manifest-csv "${EXTRACTIONS[@]}"
  "${EMBEDDING_ARGS[@]}"
  "${EMBEDDING_NPZ_ARGS[@]}"
  --final-study-embedding-manifest-csv "${STUDY_EMBEDDING_MANIFEST}"
  --structured-measurements-csv "${STRUCTURED_MEASUREMENTS}"
  --subject-split-map-csv "${SPLIT_MAP}"
  --canonical-summary "lvot_vti=${LVOT_SUMMARY}"
  --canonical-summary "tapse=${TAPSE_SUMMARY}"
  --canonical-prediction "lvot_vti=${LVOT_PREDICTIONS}"
  --canonical-prediction "tapse=${TAPSE_PREDICTIONS}"
  --duplicate-forensics-rows-csv "${DECISION_ROWS}"
  --duplicate-forensics-provenance-json "${DECISION_SUMMARY}"
  --lineage-metadata-json "${LINEAGE}"
  --output-dir "${OUT}/aggregate_safe/cohort_flow"
)

METRIC_COMMON=(
  --imaging-predictions "lvot_vti=${LVOT_PREDICTIONS}"
  --imaging-predictions "tapse=${TAPSE_PREDICTIONS}"
  --output-dir "${OUT}/aggregate_safe/reviewer_metrics"
  --restricted-input-provenance-json "${OUT}/restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json"
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
      "[ok] selected universe, measurements, split, study/clip embeddings, checkpoint, predictions, and audit roots exist" \
      "[ok] lineage-repair and duplicate-corrected output roots are explicit and separate"
    ;;
  validate)
    if [[ -f "${DECISION_ROWS}" && -f "${DECISION_SUMMARY}" ]]; then
      "${PY}" scripts/reconstruct_jdim_cohort_flow.py "${COHORT_COMMON[@]}" --schema-only
    else
      echo "[deferred] cohort schema/hash validation awaits duplicate-forensics outputs"
    fi
    "${PY}" scripts/compute_jdim_fixed_prediction_metrics.py "${METRIC_COMMON[@]}" --schema-only
    "${PY}" scripts/prepare_jdim_input_audit.py validate-config --config "${CONFIG}"
    if [[ -f "${OUT}/restricted/cohort_flow/jdim_target_cohort_lvot_vti.csv" && \
          -f "${OUT}/restricted/cohort_flow/jdim_target_cohort_tapse.csv" ]]; then
      "${PY}" scripts/prepare_jdim_input_audit.py validate-inputs \
        --config "${CONFIG}" \
        --cohort "lvot_vti=${OUT}/restricted/cohort_flow/jdim_target_cohort_lvot_vti.csv" \
        --cohort "tapse=${OUT}/restricted/cohort_flow/jdim_target_cohort_tapse.csv" \
        --canonical-clip-manifest-csv "${CLIP_EMBEDDING_MANIFEST}"
    fi
    ;;
  cohort-flow)
    "${PY}" scripts/reconstruct_jdim_cohort_flow.py \
      "${COHORT_COMMON[@]}" \
      --restricted-reconciliation-csv "${OUT}/restricted/cohort_flow/reconciliation.csv"
    ;;
  reviewer-metrics)
    if [[ -e "${OUT}/aggregate_safe/reviewer_metrics" || \
          -e "${OUT}/restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json" ]]; then
      echo "[error] Refusing to overwrite fixed original reviewer metrics" >&2
      exit 2
    fi
    "${PY}" scripts/compute_jdim_fixed_prediction_metrics.py "${METRIC_COMMON[@]}"
    ;;
  duplicate-forensics)
    "${PY}" scripts/forensic_jdim_duplicate_keys.py \
      "${FORENSIC_ARGS[@]}" \
      --subject-split-map-csv "${SPLIT_MAP}" \
      --target-cohort "lvot_vti=${LVOT_PREDICTIONS}" \
      --target-cohort "tapse=${TAPSE_PREDICTIONS}" \
      --restricted-output-dir "${OUT}/restricted/duplicate_forensics" \
      --safe-output-dir "${OUT}/aggregate_safe/duplicate_forensics"
    ;;
  corrected-aggregation)
    require_file "${DECISION_ROWS}"
    require_file "${DECISION_SUMMARY}"
    "${PY}" scripts/reconstruct_jdim_cohort_flow.py "${COHORT_COMMON[@]}" --schema-only
    "${PY}" scripts/build_jdim_corrected_study_embeddings.py \
      --forensic-evidence-file "${DECISION_ROWS}" \
      --forensic-provenance-json "${DECISION_SUMMARY}" \
      --clip-manifest-csv "${CLIP_EMBEDDING_MANIFEST}" \
      --clip-embedding-npz "${CLIP_EMBEDDING_NPZ}" \
      --frozen-study-manifest-csv "${STUDY_EMBEDDING_MANIFEST}" \
      --frozen-study-embedding-npz "${STUDY_EMBEDDING_NPZ}" \
      --output-root "${CORRECTED_ROOT}/aggregation"
    ;;
  corrected-analysis)
    CORRECTED_STUDY_NPZ="${CORRECTED_ROOT}/aggregation/restricted/corrected_study_embeddings.npz"
    CORRECTED_STUDY_MANIFEST="${CORRECTED_ROOT}/aggregation/restricted/corrected_study_embedding_manifest.csv"
    require_file "${CORRECTED_STUDY_NPZ}"
    require_file "${CORRECTED_STUDY_MANIFEST}"
    for output_dir in \
      "${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips" \
      "${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips" \
      "${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips_exclude_hard_extremes" \
      "${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips_exclude_hard_extremes" \
      "${CORRECTED_ROOT}/aggregate_safe/reviewer_metrics" \
      "${CORRECTED_ROOT}/restricted/reviewer_metrics"; do
      if [[ -e "${output_dir}" ]]; then
        echo "[error] Refusing to overwrite corrected analysis output: ${output_dir}" >&2
        exit 2
      fi
    done
    for target in lvot_vti tapse; do
      "${PY}" scripts/run_tapse_lvot_vti_imaging_baseline.py \
        --structured-measurements-csv "${STRUCTURED_MEASUREMENTS}" \
        --study-embedding-npz "${CORRECTED_STUDY_NPZ}" \
        --study-embedding-manifest "${CORRECTED_STUDY_MANIFEST}" \
        --subject-split-map-csv "${SPLIT_MAP}" \
        --target "${target}" \
        --analysis-label all_clips_study_embeddings_stable_v2 \
        --ridge-solver svd \
        --standardize-features \
        --ridge-alphas "${PHASE2_RIDGE_ALPHAS}" \
        --random-seed "${PHASE2_RANDOM_SEED}" \
        --n-bootstrap "${BOOTSTRAP_N}" \
        --bootstrap-unit subject \
        --allow-restricted-patient-outputs \
        --output-dir "${CORRECTED_ROOT}/restricted/analyses/${target}/all_clips"
      "${PY}" scripts/run_tapse_lvot_vti_imaging_baseline.py \
        --structured-measurements-csv "${STRUCTURED_MEASUREMENTS}" \
        --study-embedding-npz "${CORRECTED_STUDY_NPZ}" \
        --study-embedding-manifest "${CORRECTED_STUDY_MANIFEST}" \
        --subject-split-map-csv "${SPLIT_MAP}" \
        --target "${target}" \
        --analysis-label all_clips_study_embeddings_stable_v2_exclude_hard_extremes \
        --ridge-solver svd \
        --standardize-features \
        --ridge-alphas "${PHASE2_RIDGE_ALPHAS}" \
        --random-seed "${PHASE2_RANDOM_SEED}" \
        --n-bootstrap "${BOOTSTRAP_N}" \
        --bootstrap-unit subject \
        --exclude-hard-extremes \
        --allow-restricted-patient-outputs \
        --output-dir "${CORRECTED_ROOT}/restricted/analyses/${target}/all_clips_exclude_hard_extremes"
    done
    CORRECTED_METRIC_ARGS=(
      --imaging-predictions "lvot_vti=${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips/imaging_baseline_predictions.csv"
      --imaging-predictions "tapse=${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips/imaging_baseline_predictions.csv"
      --output-dir "${CORRECTED_ROOT}/aggregate_safe/reviewer_metrics"
      --restricted-input-provenance-json "${CORRECTED_ROOT}/restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json"
      --bootstrap-n "${BOOTSTRAP_N}"
      --bootstrap-seed 20260824
      --restricted-inputs-acknowledged
    )
    if [[ -n "${JDIM_NONIMAGE_PREDICTIONS:-}" ]]; then
      read -r -a NONIMAGE_ITEMS <<< "${JDIM_NONIMAGE_PREDICTIONS}"
      for item in "${NONIMAGE_ITEMS[@]}"; do
        CORRECTED_METRIC_ARGS+=(--nonimage-predictions "${item}")
      done
    fi
    "${PY}" scripts/compute_jdim_fixed_prediction_metrics.py "${CORRECTED_METRIC_ARGS[@]}"
    ;;
  corrected-comparison)
    for path in \
      "${OUT}/aggregate_safe/reviewer_metrics" \
      "${CORRECTED_ROOT}/aggregate_safe/reviewer_metrics" \
      "${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips" \
      "${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips" \
      "${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips_exclude_hard_extremes" \
      "${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips_exclude_hard_extremes"; do
      require_directory "${path}"
    done
    "${PY}" scripts/compare_jdim_original_corrected.py \
      --original-predictions "lvot_vti=${LVOT_PREDICTIONS}" \
      --original-predictions "tapse=${TAPSE_PREDICTIONS}" \
      --corrected-predictions "lvot_vti=${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips/imaging_baseline_predictions.csv" \
      --corrected-predictions "tapse=${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips/imaging_baseline_predictions.csv" \
      --original-summary "lvot_vti=${LVOT_SUMMARY}" \
      --original-summary "tapse=${TAPSE_SUMMARY}" \
      --corrected-summary "lvot_vti=${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips/imaging_baseline_summary.json" \
      --corrected-summary "tapse=${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips/imaging_baseline_summary.json" \
      --original-results "lvot_vti=${PHASE2}/lvot_vti/all_clips" \
      --original-results "tapse=${PHASE2}/tapse/all_clips" \
      --original-results "reviewer_metrics=${OUT}/aggregate_safe/reviewer_metrics" \
      --corrected-results "lvot_vti=${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips" \
      --corrected-results "tapse=${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips" \
      --corrected-results "reviewer_metrics=${CORRECTED_ROOT}/aggregate_safe/reviewer_metrics" \
      --frozen-split-map-csv "${SPLIT_MAP}" \
      --original-reviewer-input-provenance-json "${OUT}/restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json" \
      --corrected-reviewer-input-provenance-json "${CORRECTED_ROOT}/restricted/reviewer_metrics/fixed_prediction_metrics_input_provenance_restricted.json" \
      --restricted-input-provenance-json "${CORRECTED_ROOT}/restricted/comparisons/original_vs_corrected_main/input_provenance_restricted.json" \
      --output-root "${CORRECTED_ROOT}/aggregate_safe/original_vs_corrected_main"
    "${PY}" scripts/compare_jdim_original_corrected.py \
      --original-predictions "lvot_vti=${PHASE2}/lvot_vti/all_clips_exclude_hard_extremes/imaging_baseline_predictions.csv" \
      --original-predictions "tapse=${PHASE2}/tapse/all_clips_exclude_hard_extremes/imaging_baseline_predictions.csv" \
      --corrected-predictions "lvot_vti=${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips_exclude_hard_extremes/imaging_baseline_predictions.csv" \
      --corrected-predictions "tapse=${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips_exclude_hard_extremes/imaging_baseline_predictions.csv" \
      --original-summary "lvot_vti=${PHASE2}/lvot_vti/all_clips_exclude_hard_extremes/imaging_baseline_summary.json" \
      --original-summary "tapse=${PHASE2}/tapse/all_clips_exclude_hard_extremes/imaging_baseline_summary.json" \
      --corrected-summary "lvot_vti=${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips_exclude_hard_extremes/imaging_baseline_summary.json" \
      --corrected-summary "tapse=${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips_exclude_hard_extremes/imaging_baseline_summary.json" \
      --original-results "lvot_vti=${PHASE2}/lvot_vti/all_clips_exclude_hard_extremes" \
      --original-results "tapse=${PHASE2}/tapse/all_clips_exclude_hard_extremes" \
      --corrected-results "lvot_vti=${CORRECTED_ROOT}/restricted/analyses/lvot_vti/all_clips_exclude_hard_extremes" \
      --corrected-results "tapse=${CORRECTED_ROOT}/restricted/analyses/tapse/all_clips_exclude_hard_extremes" \
      --frozen-split-map-csv "${SPLIT_MAP}" \
      --restricted-input-provenance-json "${CORRECTED_ROOT}/restricted/comparisons/original_vs_corrected_hard_extremes/input_provenance_restricted.json" \
      --output-root "${CORRECTED_ROOT}/aggregate_safe/original_vs_corrected_hard_extremes"
    "${PY}" scripts/validate_jdim_corrected_completion.py \
      --corrected-root "${CORRECTED_ROOT}" \
      --output-json "${CORRECTED_ROOT}/aggregate_safe/corrected_analysis_completion_v1.json"
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
      --canonical-clip-manifest-csv "${CLIP_EMBEDDING_MANIFEST}" \
      --opaque-id-key-file "${JDIM_AUDIT_KEY_FILE}" \
      --restricted-output-root "${RESTRICTED_AUDIT_ROOT}/${AUDIT_SUFFIX}" \
      --safe-output-dir "${OUT}/aggregate_safe/input_content_audit/${AUDIT_SUFFIX}"
    ;;
  audit-reconstruct-pilot)
    require_env JDIM_DICOM_DATA_ROOT
    require_absolute_outside_repo "${JDIM_DICOM_DATA_ROOT}" "JDIM_DICOM_DATA_ROOT"
    require_directory "${JDIM_DICOM_DATA_ROOT}"
    require_file "${RESTRICTED_AUDIT_ROOT}/technical_pilot/audit_linkage.csv"
    require_file "${RESTRICTED_AUDIT_ROOT}/technical_pilot/canonical_clip_roster_restricted.csv"
    "${PY}" scripts/build_jdim_audit_reconstruction_pilot.py \
      --audit-linkage-csv "${RESTRICTED_AUDIT_ROOT}/technical_pilot/audit_linkage.csv" \
      --canonical-clip-roster-csv "${RESTRICTED_AUDIT_ROOT}/technical_pilot/canonical_clip_roster_restricted.csv" \
      --canonical-clip-manifest-csv "${CLIP_EMBEDDING_MANIFEST}" \
      --dicom-data-root "${JDIM_DICOM_DATA_ROOT}" \
      --restricted-output-root "${RESTRICTED_AUDIT_ROOT}/technical_pilot_reconstruction" \
      --safe-output-dir "${OUT}/aggregate_safe/input_content_audit/technical_pilot_reconstruction" \
      --max-studies 6 \
      --max-clips-per-study 3 \
      --failure-rate-stop 0.05
    ;;
esac

echo "[done] JDIM Tier-1 mode completed: ${MODE}"
