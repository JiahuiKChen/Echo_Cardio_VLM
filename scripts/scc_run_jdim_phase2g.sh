#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
if [[ "${MODE}" != "run" && "${MODE}" != "preflight" ]]; then
  echo "[error] Usage: scripts/scc_run_jdim_phase2g.sh [preflight|run]" >&2
  exit 2
fi

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

for name in \
  JDIM_REPO_ROOT \
  JDIM_PYTHON_BIN \
  JDIM_PHASE2G_SOURCE_COMMIT \
  JDIM_PHASE2G_OUTPUT_ROOT \
  JDIM_FULLSCALE_ROOT \
  JDIM_LEGACY_ROOT \
  JDIM_SOURCE_STUDIES_CSV \
  JDIM_SELECTED_STUDIES_CSV \
  JDIM_STRUCTURED_MEASUREMENTS_CSV \
  JDIM_SPLIT_MAP_CSV \
  JDIM_STUDY_EMBEDDING_NPZ \
  JDIM_STUDY_EMBEDDING_MANIFEST_CSV \
  JDIM_CLIP_EMBEDDING_NPZ \
  JDIM_CLIP_EMBEDDING_MANIFEST_CSV \
  JDIM_DUPLICATE_DECISION_ROOT \
  JDIM_DUPLICATE_METADATA_ROOT \
  JDIM_CANONICAL_CERTIFICATE_JSON \
  JDIM_COHORT_BLOCKER_JSON \
  JDIM_LVOT_SUMMARY_JSON \
  JDIM_TAPSE_SUMMARY_JSON \
  JDIM_LVOT_PREDICTIONS_CSV \
  JDIM_TAPSE_PREDICTIONS_CSV \
  JDIM_AUDIT_CONFIG \
  JDIM_DICOM_DATA_ROOT; do
  require_env "${name}"
done

EXPECTED_CERTIFICATE_SHA="bbd38f8f913c74ab97d005e833a470cf55f25cc0c18e0063854ea5b6528426a2"
EXPECTED_BLOCKER_SHA="a1a721e4603a4914d98ce8038d0ee679c63c30d1648b7005dc35e21915b8d47d"
EXPECTED_OUTPUT_ROOT="/restricted/project/mimicecho/outputs/jdim_cohort_audit_prep_v2"

REPO="$(cd "${JDIM_REPO_ROOT}" && pwd)"
PY="${JDIM_PYTHON_BIN}"
OUT="${JDIM_PHASE2G_OUTPUT_ROOT}"
DECISION_ROWS="${JDIM_DUPLICATE_DECISION_ROOT}/restricted/duplicate_forensics_rows.csv"
DECISION_SUMMARY="${JDIM_DUPLICATE_DECISION_ROOT}/aggregate_safe/duplicate_forensics_summary.json"
METADATA_SUMMARY="${JDIM_DUPLICATE_METADATA_ROOT}/aggregate_safe/duplicate_metadata_summary.json"
METADATA_GROUPS="${JDIM_DUPLICATE_METADATA_ROOT}/restricted/metadata_group_classification.csv"
METADATA_STAGE_ROWS="${JDIM_DUPLICATE_METADATA_ROOT}/restricted/metadata_stage_rows.csv"

if [[ "${OUT}" != "${EXPECTED_OUTPUT_ROOT}" ]]; then
  echo "[error] JDIM_PHASE2G_OUTPUT_ROOT differs from the authorized immutable root" >&2
  exit 2
fi
if [[ "$(pwd -P)" != "${REPO}" ]]; then
  cd "${REPO}"
fi
for path in \
  "${PY}" \
  "${JDIM_CANONICAL_CERTIFICATE_JSON}" \
  "${JDIM_COHORT_BLOCKER_JSON}" \
  "${DECISION_ROWS}" \
  "${DECISION_SUMMARY}" \
  "${METADATA_SUMMARY}" \
  "${METADATA_GROUPS}" \
  "${METADATA_STAGE_ROWS}" \
  "${JDIM_CLIP_EMBEDDING_MANIFEST_CSV}" \
  "${JDIM_CLIP_EMBEDDING_NPZ}" \
  "${JDIM_STUDY_EMBEDDING_MANIFEST_CSV}" \
  "${JDIM_STUDY_EMBEDDING_NPZ}"; do
  require_file "${path}"
done

"${PY}" scripts/validate_jdim_phase2g_sources.py \
  --expected-source-commit "${JDIM_PHASE2G_SOURCE_COMMIT}" \
  --canonical-certificate-json "${JDIM_CANONICAL_CERTIFICATE_JSON}" \
  --expected-certificate-sha256 "${EXPECTED_CERTIFICATE_SHA}" \
  --cohort-blocker-json "${JDIM_COHORT_BLOCKER_JSON}" \
  --expected-blocker-sha256 "${EXPECTED_BLOCKER_SHA}" \
  --duplicate-decision-rows-csv "${DECISION_ROWS}" \
  --duplicate-decision-summary-json "${DECISION_SUMMARY}" \
  --duplicate-metadata-summary-json "${METADATA_SUMMARY}" \
  --duplicate-metadata-groups-csv "${METADATA_GROUPS}" \
  --duplicate-metadata-stage-rows-csv "${METADATA_STAGE_ROWS}" \
  --corrected-clip-manifest-csv "${JDIM_CLIP_EMBEDDING_MANIFEST_CSV}" \
  --corrected-clip-embeddings-npz "${JDIM_CLIP_EMBEDDING_NPZ}" \
  --corrected-study-manifest-csv "${JDIM_STUDY_EMBEDDING_MANIFEST_CSV}" \
  --corrected-study-embeddings-npz "${JDIM_STUDY_EMBEDDING_NPZ}" \
  --proposed-output-root "${OUT}"

BATCH_SOURCE_ARGS=(--batch-source "legacy_stage_d_500=legacy")
BATCH_MANIFEST_ARGS=(
  --batch-study-manifest
  "legacy_stage_d_500=${JDIM_LEGACY_ROOT}/echoprime_embeddings_512/clip_embedding_manifest.csv"
)
shopt -s nullglob
FULLSCALE_MANIFESTS=("${JDIM_FULLSCALE_ROOT}"/batches/batch_*_embeddings/clip_embedding_manifest.csv)
if (( ${#FULLSCALE_MANIFESTS[@]} == 0 )); then
  echo "[error] no fullscale embedding manifests were found" >&2
  exit 2
fi
for path in "${FULLSCALE_MANIFESTS[@]}"; do
  batch_dir="$(basename "$(dirname "${path}")")"
  batch_name="${batch_dir%_embeddings}"
  BATCH_SOURCE_ARGS+=(--batch-source "${batch_name}=fullscale")
  BATCH_MANIFEST_ARGS+=(--batch-study-manifest "${batch_name}=${path}")
done

if [[ "${MODE}" == "preflight" ]]; then
  LINEAGE_JSON="$(mktemp /tmp/jdim_phase2g_lineage.XXXXXX.json)"
  trap 'rm -f "${LINEAGE_JSON}"' EXIT
else
  mkdir -p "${OUT}/restricted/lineage" "${OUT}/aggregate_safe" "${OUT}/restricted"
  LINEAGE_JSON="${OUT}/restricted/lineage/jdim_cohort_lineage_phase2g_v1.json"
fi

"${PY}" scripts/build_jdim_cohort_lineage_metadata.py \
  --mimic-iv-echo-release "MIMIC-IV-ECHO v1.0" \
  --source-denominator-definition "Distinct subject_id-study_id pairs in physionet-data.mimiciv_echo.echo_record_list" \
  --imaging-lineage "Legacy Stage D plus fullscale batch clip manifests; encoder-only clip embeddings mean-pooled to study level" \
  --label-lineage "Structured measurements from the pinned selected-study universe; canonical target parsing and study-level median aggregation" \
  --split-map-csv "${JDIM_SPLIT_MAP_CSV}" \
  --split-version "subject_split_map_v1" \
  --split-generator "scripts/scc_run_fullscale_pipeline.sh inline fallback: echo-ai-fixed-split-seed-v1 NumPy shuffle; 0.70/0.15/remainder" \
  "${BATCH_SOURCE_ARGS[@]}" \
  "${BATCH_MANIFEST_ARGS[@]}" \
  --allow-outside-universe-batch legacy_stage_d_500 \
  --selected-studies-csv "${JDIM_SELECTED_STUDIES_CSV}" \
  --selected-universe-selection-rule "One measurement-linked study per subject with 5-99,999 DICOM records, deterministically ranked by FARM_FINGERPRINT(CONCAT(study_id,'-',20260323)) in the frozen fullscale selection" \
  --output-json "${LINEAGE_JSON}"

export JDIM_OUTPUT_ROOT="${OUT}"
export JDIM_LINEAGE_JSON="${LINEAGE_JSON}"
export JDIM_RESTRICTED_AUDIT_ROOT="${OUT}/restricted/input_content_audit"
export JDIM_DUPLICATE_METADATA_ROOT
if [[ "${MODE}" == "preflight" ]]; then
  scripts/scc_run_jdim_tier1.sh cohort-preflight
  exit 0
fi
scripts/scc_run_jdim_tier1.sh cohort-flow

COHORT_LOCK="${OUT}/aggregate_safe/cohort_flow/cohort_flow_lock.json"
if [[ "$("${PY}" -c "import json; print(json.load(open('${COHORT_LOCK}'))['status'])")" != "JDIM_COHORT_FLOW_LOCKED" ]]; then
  echo "[error] cohort flow did not lock" >&2
  exit 2
fi

AUDIT_RESTRICTED="${OUT}/restricted/input_content_audit/locked_roster"
AUDIT_SAFE="${OUT}/aggregate_safe/input_content_audit/locked_roster"
AUDIT_KEY="${OUT}/restricted/input_content_audit/opaque_id_key.bin"
umask 077
"${PY}" -c "import os,sys; open(sys.argv[1],'wb').write(os.urandom(32))" "${AUDIT_KEY}"
"${PY}" scripts/prepare_jdim_input_audit.py sample \
  --config "${JDIM_AUDIT_CONFIG}" \
  --cohort "lvot_vti=${OUT}/restricted/cohort_flow/jdim_target_cohort_lvot_vti.csv" \
  --cohort "tapse=${OUT}/restricted/cohort_flow/jdim_target_cohort_tapse.csv" \
  --canonical-clip-manifest-csv "${JDIM_CLIP_EMBEDDING_MANIFEST_CSV}" \
  --opaque-id-key-file "${AUDIT_KEY}" \
  --restricted-output-root "${AUDIT_RESTRICTED}" \
  --safe-output-dir "${AUDIT_SAFE}" \
  --sample-size-per-target 60

ROSTER_LOCK="${AUDIT_SAFE}/audit_roster_lock.json"
if [[ "$("${PY}" -c "import json; print(json.load(open('${ROSTER_LOCK}'))['status'])")" != "AUDIT_ROSTER_LOCKED" ]]; then
  echo "[error] audit roster did not lock" >&2
  exit 2
fi

AVAIL_RESTRICTED="${OUT}/restricted/input_content_audit/source_availability"
AVAIL_SAFE="${OUT}/aggregate_safe/input_content_audit/source_availability"
"${PY}" scripts/assess_jdim_audit_source_availability.py \
  --audit-linkage-csv "${AUDIT_RESTRICTED}/audit_linkage.csv" \
  --canonical-clip-roster-csv "${AUDIT_RESTRICTED}/canonical_clip_roster_restricted.csv" \
  --canonical-clip-manifest-csv "${JDIM_CLIP_EMBEDDING_MANIFEST_CSV}" \
  --dicom-data-root "${JDIM_DICOM_DATA_ROOT}" \
  --restricted-output-root "${AVAIL_RESTRICTED}" \
  --safe-output-dir "${AVAIL_SAFE}" \
  --max-ready-pilot-studies 6

AVAIL_SUMMARY="${AVAIL_SAFE}/source_availability_summary.json"
READY_COUNT="$("${PY}" -c "import json; print(json.load(open('${AVAIL_SUMMARY}'))['studies_technically_ready_for_reconstruction'])")"
PILOT_SUMMARY=""
if (( READY_COUNT > 0 )); then
  PILOT_RESTRICTED="${OUT}/restricted/input_content_audit/technical_pilot"
  PILOT_SAFE="${OUT}/aggregate_safe/input_content_audit/technical_pilot"
  set +e
  "${PY}" scripts/build_jdim_audit_reconstruction_pilot.py \
    --audit-linkage-csv "${AVAIL_RESTRICTED}/technically_ready_pilot_linkage.csv" \
    --canonical-clip-roster-csv "${AUDIT_RESTRICTED}/canonical_clip_roster_restricted.csv" \
    --canonical-clip-manifest-csv "${JDIM_CLIP_EMBEDDING_MANIFEST_CSV}" \
    --dicom-data-root "${JDIM_DICOM_DATA_ROOT}" \
    --restricted-output-root "${PILOT_RESTRICTED}" \
    --safe-output-dir "${PILOT_SAFE}" \
    --max-studies 6 \
    --max-clips-per-study 3
  PILOT_EXIT=$?
  set -e
  if [[ -f "${PILOT_SAFE}/reconstruction_pilot_summary.json" ]]; then
    PILOT_SUMMARY="${PILOT_SAFE}/reconstruction_pilot_summary.json"
  elif (( PILOT_EXIT == 0 )); then
    echo "[error] pilot reported success without a summary" >&2
    exit 2
  fi
fi

FINALIZE_ARGS=(
  --source-commit "${JDIM_PHASE2G_SOURCE_COMMIT}"
  --cohort-lock-json "${COHORT_LOCK}"
  --roster-lock-json "${ROSTER_LOCK}"
  --source-availability-json "${AVAIL_SUMMARY}"
  --output-json "${OUT}/aggregate_safe/phase2g_stage_status.json"
)
if [[ -n "${PILOT_SUMMARY}" ]]; then
  FINALIZE_ARGS+=(--pilot-summary-json "${PILOT_SUMMARY}")
fi
"${PY}" scripts/finalize_jdim_phase2g_status.py "${FINALIZE_ARGS[@]}"
