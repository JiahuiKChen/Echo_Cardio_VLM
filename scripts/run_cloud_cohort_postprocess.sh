#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Run postprocessing after run_cloud_echo_cohort.sh:
1) key-frame selection from extracted smoke clips
2) structured_measurement export for selected studies
3) LVEF-labeled still-image manifest build
4) lightweight still-image baseline run

Usage:
  ./scripts/run_cloud_cohort_postprocess.sh \
    --billing-project <gcp_project> \
    [--cohort-root <outputs/cloud_cohorts/stage_c_50study>] \
    [--download-root </Volumes/.../stage_c_50study>] \
    [--strict-download-root true|false] \
    [--min-free-gb 10] \
    [--python-bin <.venv-echoprime/bin/python>] \
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

volume_mount_for_path() {
  local path="$1"
  local suffix
  suffix="${path#/Volumes/}"
  if [[ "${suffix}" == "${path}" ]]; then
    echo ""
    return 0
  fi
  local vol
  vol="${suffix%%/*}"
  echo "/Volumes/${vol}"
}

ensure_min_free_gb() {
  local path="$1"
  local min_free_gb="$2"
  if [[ "${min_free_gb}" -le 0 ]]; then
    return 0
  fi
  local avail_kb
  avail_kb="$(df -Pk "${path}" | awk 'NR==2 {print $4}')"
  if [[ -z "${avail_kb}" ]]; then
    echo "[warn] Could not determine free space for ${path}; continuing." >&2
    return 0
  fi
  local avail_gb
  avail_gb=$((avail_kb / 1024 / 1024))
  if [[ "${avail_gb}" -lt "${min_free_gb}" ]]; then
    echo "[error] Insufficient free space at ${path}: ${avail_gb} GB available, need at least ${min_free_gb} GB." >&2
    exit 1
  fi
  echo "[info] Free space check passed at ${path}: ${avail_gb} GB available (threshold ${min_free_gb} GB)."
}

BILLING_PROJECT=""
COHORT_ROOT="${REPO_ROOT}/outputs/cloud_cohorts/stage_c_50study"
ECHO_AI_DATA_ROOT="${ECHO_AI_DATA_ROOT:-${REPO_ROOT}/outputs/data}"
DOWNLOAD_ROOT="${ECHO_AI_DATA_ROOT}/cloud_cohorts/stage_c_50study"
STRICT_DOWNLOAD_ROOT="true"
MIN_FREE_GB=10
DEFAULT_PYTHON_BIN="${REPO_ROOT}/.venv-echoprime/bin/python"
if [[ ! -x "${DEFAULT_PYTHON_BIN}" ]]; then
  DEFAULT_PYTHON_BIN="python3"
fi
PYTHON_BIN="${DEFAULT_PYTHON_BIN}"
RUN_BASELINE="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --billing-project) BILLING_PROJECT="$2"; shift 2 ;;
    --cohort-root) COHORT_ROOT="$2"; shift 2 ;;
    --download-root) DOWNLOAD_ROOT="$2"; shift 2 ;;
    --strict-download-root) STRICT_DOWNLOAD_ROOT="$(to_bool "$2")"; shift 2 ;;
    --min-free-gb) MIN_FREE_GB="$2"; shift 2 ;;
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

if [[ -z "${BILLING_PROJECT}" ]]; then
  echo "[error] --billing-project is required." >&2
  usage
  exit 1
fi

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

mount_point="$(volume_mount_for_path "${DOWNLOAD_ROOT}")"
if [[ -n "${mount_point}" && ! -d "${mount_point}" ]]; then
  if [[ "${STRICT_DOWNLOAD_ROOT}" == "true" ]]; then
    echo "[error] External volume appears unmounted: ${mount_point}" >&2
    echo "[error] Refusing to proceed because --strict-download-root=true." >&2
    exit 1
  fi
  echo "[warn] External volume appears unmounted but continuing because --strict-download-root=false." >&2
fi

if [[ ! -d "${DOWNLOAD_ROOT}" ]]; then
  if [[ "${STRICT_DOWNLOAD_ROOT}" == "true" ]]; then
    echo "[error] Download root not found: ${DOWNLOAD_ROOT}" >&2
    echo "[error] Refusing to proceed because --strict-download-root=true." >&2
    exit 1
  fi
  mkdir -p "${DOWNLOAD_ROOT}"
fi

ensure_min_free_gb "${DOWNLOAD_ROOT}" "${MIN_FREE_GB}"

EXTRACT_MANIFEST="${COHORT_ROOT}/extract_smoke/extraction_manifest.csv"
SELECTED_STUDIES="${COHORT_ROOT}/manifests/selected_studies.csv"
if [[ ! -f "${EXTRACT_MANIFEST}" ]]; then
  echo "[error] Missing extraction manifest: ${EXTRACT_MANIFEST}" >&2
  exit 1
fi
if [[ ! -f "${SELECTED_STUDIES}" ]]; then
  echo "[error] Missing selected studies CSV: ${SELECTED_STUDIES}" >&2
  exit 1
fi

KEYFRAME_ROOT="${DOWNLOAD_ROOT}/derived/keyframes_smoke"
KEYFRAME_MANIFEST="${COHORT_ROOT}/extract_smoke/keyframe_manifest.csv"
MEAS_CSV="${COHORT_ROOT}/manifests/structured_measurements.csv"
MEAS_SQL="${COHORT_ROOT}/queries/export_structured_measurements.sql"
SUBJECT_SPLIT_MAP="${COHORT_ROOT}/manifests/subject_split_map_v1.csv"
LVEF_MANIFEST="${COHORT_ROOT}/manifests/lvef_still_manifest.csv"
BASELINE_OUT="${COHORT_ROOT}/baseline_lvef_still"

echo "[info] Selecting key frames..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/select_keyframes_from_npz.py" \
  --extraction-manifest "${EXTRACT_MANIFEST}" \
  --output-root "${KEYFRAME_ROOT}" \
  --output-manifest "${KEYFRAME_MANIFEST}" \
  --method combo_sharp_still

echo "[info] Exporting structured measurements..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/export_cohort_measurements.py" \
  --selected-studies-csv "${SELECTED_STUDIES}" \
  --billing-project "${BILLING_PROJECT}" \
  --output-csv "${MEAS_CSV}" \
  --query-sql-out "${MEAS_SQL}"

echo "[info] Building frozen subject split map (v1)..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/global_subject_split_v1.py" \
  --input-csv "${SELECTED_STUDIES}" \
  --subject-column subject_id \
  --output-csv "${SUBJECT_SPLIT_MAP}" \
  --overwrite

echo "[info] Building LVEF still-image manifest..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/build_lvef_still_manifest.py" \
  --selected-studies-csv "${SELECTED_STUDIES}" \
  --structured-measurements-csv "${MEAS_CSV}" \
  --keyframe-manifest-csv "${KEYFRAME_MANIFEST}" \
  --subject-split-map-csv "${SUBJECT_SPLIT_MAP}" \
  --output-csv "${LVEF_MANIFEST}"

if [[ "${RUN_BASELINE}" == "true" ]]; then
  echo "[info] Running lightweight baseline..."
  "${PYTHON_BIN}" "${SCRIPT_DIR}/run_lvef_still_baseline.py" \
    --manifest-csv "${LVEF_MANIFEST}" \
    --output-dir "${BASELINE_OUT}"
else
  echo "[info] Skipping lightweight baseline (--run-baseline=false)."
fi

echo "[done] Postprocess pipeline completed."
echo "[done] Keyframe manifest: ${KEYFRAME_MANIFEST}"
echo "[done] Measurements CSV: ${MEAS_CSV}"
echo "[done] Subject split map: ${SUBJECT_SPLIT_MAP}"
echo "[done] LVEF manifest: ${LVEF_MANIFEST}"
if [[ "${RUN_BASELINE}" == "true" ]]; then
  echo "[done] Baseline metrics: ${BASELINE_OUT}/lvef_still_baseline_metrics.json"
fi
