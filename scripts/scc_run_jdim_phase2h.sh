#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
if [[ "${MODE}" != "run" && "${MODE}" != "preflight" ]]; then
  echo "[error] Usage: scripts/scc_run_jdim_phase2h.sh [preflight|run]" >&2
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

verify_sha256() {
  local path="$1"
  local expected="$2"
  local observed
  observed="$(sha256sum "${path}" | awk '{print $1}')"
  if [[ "${observed}" != "${expected}" ]]; then
    echo "[error] Locked evidence hash mismatch: ${path}" >&2
    exit 2
  fi
}

for name in \
  JDIM_REPO_ROOT \
  JDIM_PYTHON_BIN \
  JDIM_PHASE2H_SOURCE_COMMIT \
  JDIM_PHASE2H_OUTPUT_ROOT \
  JDIM_LOCKED_COHORT_ROOT \
  JDIM_CANONICAL_CERTIFICATE_JSON \
  JDIM_CLIP_EMBEDDING_MANIFEST_CSV \
  JDIM_AUDIT_CONFIG \
  JDIM_DICOM_DATA_ROOT; do
  require_env "${name}"
done

EXPECTED_OUTPUT_ROOT="/restricted/project/mimicecho/outputs/jdim_audit_roster_pilot_v1"
EXPECTED_COHORT_ROOT="/restricted/project/mimicecho/outputs/jdim_cohort_audit_prep_v2/aggregate_safe/cohort_flow"
EXPECTED_CERTIFICATE_SHA="bbd38f8f913c74ab97d005e833a470cf55f25cc0c18e0063854ea5b6528426a2"
EXPECTED_LOCK_SHA="fb528a9611cf512b305c431ceffd705b6680ca9a24dadc14080731786933f233"
EXPECTED_SUMMARY_SHA="aa8bda7991e341cfdc5cfb84407c3005bb01062258211f886d1c98c53d8014c8"
EXPECTED_INVARIANTS_SHA="13a0fdfe06c92e441c6e0c30c1ed4722e94954e4869399daff4333a261ab45fa"

REPO="$(cd "${JDIM_REPO_ROOT}" && pwd)"
PY="${JDIM_PYTHON_BIN}"
OUT="${JDIM_PHASE2H_OUTPUT_ROOT}"
COHORT_ROOT="${JDIM_LOCKED_COHORT_ROOT}"
COHORT_RUN_ROOT="$(dirname "$(dirname "${COHORT_ROOT}")")"
LVOT_COHORT="${COHORT_RUN_ROOT}/restricted/cohort_flow/jdim_target_cohort_lvot_vti.csv"
TAPSE_COHORT="${COHORT_RUN_ROOT}/restricted/cohort_flow/jdim_target_cohort_tapse.csv"

if [[ "${OUT}" != "${EXPECTED_OUTPUT_ROOT}" || "${COHORT_ROOT}" != "${EXPECTED_COHORT_ROOT}" ]]; then
  echo "[error] Phase 2H output or locked cohort root differs from authorization" >&2
  exit 2
fi
if [[ "$(git -C "${REPO}" rev-parse HEAD)" != "${JDIM_PHASE2H_SOURCE_COMMIT}" ]]; then
  echo "[error] SCC checkout is not at the authorized Phase 2H commit" >&2
  exit 2
fi
if [[ -n "$(git -C "${REPO}" status --porcelain)" ]]; then
  echo "[error] SCC checkout is not clean" >&2
  exit 2
fi
if [[ -e "${OUT}" ]]; then
  echo "[error] refusing to overwrite the immutable Phase 2H output root" >&2
  exit 2
fi

for path in \
  "${PY}" \
  "${JDIM_CANONICAL_CERTIFICATE_JSON}" \
  "${COHORT_ROOT}/cohort_flow_lock.json" \
  "${COHORT_ROOT}/cohort_flow_summary.json" \
  "${COHORT_ROOT}/cohort_flow_invariants.json" \
  "${LVOT_COHORT}" \
  "${TAPSE_COHORT}" \
  "${JDIM_CLIP_EMBEDDING_MANIFEST_CSV}" \
  "${JDIM_AUDIT_CONFIG}"; do
  require_file "${path}"
done
verify_sha256 "${JDIM_CANONICAL_CERTIFICATE_JSON}" "${EXPECTED_CERTIFICATE_SHA}"
verify_sha256 "${COHORT_ROOT}/cohort_flow_lock.json" "${EXPECTED_LOCK_SHA}"
verify_sha256 "${COHORT_ROOT}/cohort_flow_summary.json" "${EXPECTED_SUMMARY_SHA}"
verify_sha256 "${COHORT_ROOT}/cohort_flow_invariants.json" "${EXPECTED_INVARIANTS_SHA}"

cd "${REPO}"
"${PY}" scripts/prepare_jdim_input_audit.py validate-config --config "${JDIM_AUDIT_CONFIG}"
"${PY}" scripts/prepare_jdim_input_audit.py validate-inputs \
  --config "${JDIM_AUDIT_CONFIG}" \
  --cohort "lvot_vti=${LVOT_COHORT}" \
  --cohort "tapse=${TAPSE_COHORT}" \
  --canonical-clip-manifest-csv "${JDIM_CLIP_EMBEDDING_MANIFEST_CSV}"
if [[ "${MODE}" == "preflight" ]]; then
  echo "PHASE2H_ACTUAL_SCHEMA_PREFLIGHT_OK"
  exit 0
fi

mkdir "${OUT}"
mkdir -p "${OUT}/aggregate_safe/input_content_audit"
AUDIT_KEY="${OUT}/restricted/input_content_audit/opaque_id_key.bin"
"${PY}" scripts/create_jdim_audit_key.py \
  --output-root "${OUT}" \
  --key-file "${AUDIT_KEY}" \
  --run-id "phase2h:${JDIM_PHASE2H_SOURCE_COMMIT}"

AUDIT_RESTRICTED="${OUT}/restricted/input_content_audit/locked_roster"
AUDIT_SAFE="${OUT}/aggregate_safe/input_content_audit/locked_roster"
"${PY}" scripts/prepare_jdim_input_audit.py sample \
  --config "${JDIM_AUDIT_CONFIG}" \
  --cohort "lvot_vti=${LVOT_COHORT}" \
  --cohort "tapse=${TAPSE_COHORT}" \
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
echo "AUDIT_ROSTER_LOCKED"

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
if (( READY_COUNT == 0 )); then
  echo "BLOCKED_AUDIT_SOURCE_RESTORATION"
  exit 0
fi

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
if (( PILOT_EXIT == 0 )); then
  echo "AUDIT_TECHNICAL_PILOT_READY"
else
  echo "BLOCKED_AUDIT_RECONSTRUCTION"
fi
exit 0
