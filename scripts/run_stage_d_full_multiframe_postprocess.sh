#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Run postprocess steps for Stage D full-multiframe extraction:
1) key-frame selection from full extraction manifest
2) full still-image manifest build (reusing selected studies + measurements + subject splits)
3) optional lightweight still-image baseline

Usage:
  ./scripts/run_stage_d_full_multiframe_postprocess.sh \
    [--cohort-root outputs/cloud_cohorts/stage_d_500study] \
    [--download-root '/Volumes/MIMIC ECHO Drive/echo_ai/cloud_cohorts/stage_d_500study'] \
    [--python-bin .venv-echoprime/bin/python] \
    [--run-baseline true|false]
EOF
}

to_bool() {
  local value="${1:-}"
  local normalized
  normalized="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
  case "${normalized}" in
    true|1|yes|y) echo "true" ;;
    false|0|no|n) echo "false" ;;
    *)
      echo "[error] Invalid boolean value: ${value}" >&2
      exit 1
      ;;
  esac
}

COHORT_ROOT="${REPO_ROOT}/outputs/cloud_cohorts/stage_d_500study"
DOWNLOAD_ROOT="/Volumes/MIMIC ECHO Drive/echo_ai/cloud_cohorts/stage_d_500study"
DEFAULT_PYTHON_BIN="${REPO_ROOT}/.venv-echoprime/bin/python"
if [[ ! -x "${DEFAULT_PYTHON_BIN}" ]]; then
  DEFAULT_PYTHON_BIN="python3"
fi
PYTHON_BIN="${DEFAULT_PYTHON_BIN}"
RUN_BASELINE="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cohort-root) COHORT_ROOT="$2"; shift 2 ;;
    --download-root) DOWNLOAD_ROOT="$2"; shift 2 ;;
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --run-baseline) RUN_BASELINE="$(to_bool "$2")"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "${PYTHON_BIN}" == */* ]]; then
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "[error] Python executable not found/executable: ${PYTHON_BIN}" >&2
    exit 1
  fi
else
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "[error] Python command not found in PATH: ${PYTHON_BIN}" >&2
    exit 1
  fi
fi

EXTRACT_MANIFEST="${COHORT_ROOT}/extract_full/extraction_manifest.csv"
KEYFRAME_ROOT="${DOWNLOAD_ROOT}/derived/keyframes_full"
KEYFRAME_MANIFEST="${COHORT_ROOT}/extract_full/keyframe_manifest.csv"
SELECTED_STUDIES="${COHORT_ROOT}/manifests/selected_studies.csv"
MEAS_CSV="${COHORT_ROOT}/manifests/structured_measurements.csv"
SUBJECT_SPLIT_MAP="${COHORT_ROOT}/manifests/subject_split_map_v1.csv"
LVEF_MANIFEST="${COHORT_ROOT}/manifests/lvef_still_manifest_full.csv"
BASELINE_OUT="${COHORT_ROOT}/baseline_lvef_still_full"

for required in "${EXTRACT_MANIFEST}" "${SELECTED_STUDIES}" "${MEAS_CSV}" "${SUBJECT_SPLIT_MAP}"; do
  if [[ ! -f "${required}" ]]; then
    echo "[error] Required file not found: ${required}" >&2
    exit 1
  fi
done

mkdir -p "${COHORT_ROOT}/extract_full" "${KEYFRAME_ROOT}" "${BASELINE_OUT}"

echo "[info] Selecting key frames from full extraction..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/select_keyframes_from_npz.py" \
  --extraction-manifest "${EXTRACT_MANIFEST}" \
  --output-root "${KEYFRAME_ROOT}" \
  --output-manifest "${KEYFRAME_MANIFEST}" \
  --method combo_sharp_still

echo "[info] Building full LVEF still-image manifest..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/build_lvef_still_manifest.py" \
  --selected-studies-csv "${SELECTED_STUDIES}" \
  --structured-measurements-csv "${MEAS_CSV}" \
  --keyframe-manifest-csv "${KEYFRAME_MANIFEST}" \
  --subject-split-map-csv "${SUBJECT_SPLIT_MAP}" \
  --output-csv "${LVEF_MANIFEST}"

if [[ "${RUN_BASELINE}" == "true" ]]; then
  echo "[info] Running full-manifest still baseline..."
  "${PYTHON_BIN}" "${SCRIPT_DIR}/run_lvef_still_baseline.py" \
    --manifest-csv "${LVEF_MANIFEST}" \
    --output-dir "${BASELINE_OUT}"
else
  echo "[info] Skipping baseline (--run-baseline=false)."
fi

echo "[done] Full multiframe postprocess complete."
echo "[done] Keyframe manifest: ${KEYFRAME_MANIFEST}"
echo "[done] LVEF still manifest: ${LVEF_MANIFEST}"
if [[ "${RUN_BASELINE}" == "true" ]]; then
  echo "[done] Baseline metrics: ${BASELINE_OUT}/lvef_still_baseline_metrics.json"
fi
