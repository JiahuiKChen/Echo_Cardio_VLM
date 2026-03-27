#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Run a small SCC canary cohort end-to-end (2 studies).

Prerequisites:
  1) source scc_env.sh
  2) module load python3/3.10.12
  3) module load google-cloud-sdk/455.0.0
  4) gcloud auth login completed on SCC

Usage:
  ./scripts/scc_run_canary_2study.sh [--billing-project mimic-iv-anesthesia]
EOF
}

BILLING_PROJECT="${ECHO_AI_BILLING_PROJECT:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --billing-project) BILLING_PROJECT="$2"; shift 2 ;;
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

./scripts/run_cloud_echo_cohort.sh \
  --billing-project "${BILLING_PROJECT}" \
  --cohort-root "${REPO_ROOT}/outputs/cloud_cohorts/scc_canary_2study" \
  --download-root "${ECHO_AI_DATA_ROOT}/cloud_cohorts/scc_canary_2study" \
  --n-studies 2 \
  --seed 20260326 \
  --min-dicoms 40 \
  --max-dicoms 140 \
  --max-studies-per-subject 1 \
  --require-note-link true \
  --require-measurement-link true \
  --gcs-bucket "mimic-iv-echo-1.0.physionet.org" \
  --extract-max-clips 20 \
  --extract-max-clips-per-study 1

./scripts/run_cloud_cohort_postprocess.sh \
  --billing-project "${BILLING_PROJECT}" \
  --cohort-root "${REPO_ROOT}/outputs/cloud_cohorts/scc_canary_2study" \
  --download-root "${ECHO_AI_DATA_ROOT}/cloud_cohorts/scc_canary_2study" \
  --strict-download-root true \
  --min-free-gb 5 \
  --run-baseline false

echo "[done] SCC canary run completed."
