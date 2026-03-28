#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Run Stage D (500-study) SCC cohort pipeline.

Prerequisites:
  1) source scc_env.sh
  2) module load python3/3.10.12
  3) module load google-cloud-sdk/455.0.0
  4) gcloud auth login completed on SCC

Usage:
  ./scripts/scc_run_stage_d_500study.sh \
    [--billing-project mimic-iv-anesthesia] \
    [--run-baseline true|false] \
    [--extract-max-clips 1000] \
    [--extract-max-clips-per-study 2] \
    [--extract-num-workers 1]
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

BILLING_PROJECT="${ECHO_AI_BILLING_PROJECT:-}"
RUN_BASELINE="false"
EXTRACT_MAX_CLIPS=1000
EXTRACT_MAX_CLIPS_PER_STUDY=2
EXTRACT_NUM_WORKERS=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --billing-project) BILLING_PROJECT="$2"; shift 2 ;;
    --run-baseline) RUN_BASELINE="$(to_bool "$2")"; shift 2 ;;
    --extract-max-clips) EXTRACT_MAX_CLIPS="$2"; shift 2 ;;
    --extract-max-clips-per-study) EXTRACT_MAX_CLIPS_PER_STUDY="$2"; shift 2 ;;
    --extract-num-workers) EXTRACT_NUM_WORKERS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${BILLING_PROJECT}" ]]; then
  echo "[error] billing project is required (use --billing-project or ECHO_AI_BILLING_PROJECT)." >&2
  exit 1
fi

detect_data_root() {
  local project_name="${ECHO_AI_PROJECT_NAME:-mimicecho}"
  local candidate
  for candidate in \
    "/restricted/projectnb/${project_name}/echo_ai_data" \
    "/projectnb/${project_name}/echo_ai_data"
  do
    if [[ -d "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
    if mkdir -p "${candidate}" 2>/dev/null; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

if [[ -z "${ECHO_AI_DATA_ROOT:-}" ]]; then
  if DETECTED_DATA_ROOT="$(detect_data_root)"; then
    export ECHO_AI_DATA_ROOT="${DETECTED_DATA_ROOT}"
    echo "[warn] ECHO_AI_DATA_ROOT was not set; auto-detected ${ECHO_AI_DATA_ROOT}"
  else
    echo "[error] ECHO_AI_DATA_ROOT is not set and no projectnb path could be auto-detected." >&2
    echo "[error] Run: source ${REPO_ROOT}/scc_env.sh" >&2
    exit 1
  fi
fi

COHORT_ROOT="${REPO_ROOT}/outputs/cloud_cohorts/stage_d_500study_scc"
DOWNLOAD_ROOT="${ECHO_AI_DATA_ROOT}/cloud_cohorts/stage_d_500study_scc"

./scripts/run_cloud_echo_cohort.sh \
  --billing-project "${BILLING_PROJECT}" \
  --cohort-root "${COHORT_ROOT}" \
  --download-root "${DOWNLOAD_ROOT}" \
  --n-studies 500 \
  --seed 20260323 \
  --min-dicoms 40 \
  --max-dicoms 140 \
  --max-studies-per-subject 1 \
  --require-note-link true \
  --require-measurement-link true \
  --gcs-bucket "mimic-iv-echo-1.0.physionet.org" \
  --extract-max-clips "${EXTRACT_MAX_CLIPS}" \
  --extract-max-clips-per-study "${EXTRACT_MAX_CLIPS_PER_STUDY}" \
  --extract-num-workers "${EXTRACT_NUM_WORKERS}"

./scripts/run_cloud_cohort_postprocess.sh \
  --billing-project "${BILLING_PROJECT}" \
  --cohort-root "${COHORT_ROOT}" \
  --download-root "${DOWNLOAD_ROOT}" \
  --strict-download-root true \
  --min-free-gb 20 \
  --run-baseline "${RUN_BASELINE}"

echo "[done] Stage D SCC run completed."
