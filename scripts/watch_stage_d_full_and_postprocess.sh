#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Wait for Stage D full extraction manifest, then run postprocess.

Usage:
  ./scripts/watch_stage_d_full_and_postprocess.sh \
    [--cohort-root outputs/cloud_cohorts/stage_d_500study] \
    [--download-root '/Volumes/MIMIC ECHO Drive/echo_ai/cloud_cohorts/stage_d_500study'] \
    [--poll-seconds 120] \
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
POLL_SECONDS=120
RUN_BASELINE="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cohort-root) COHORT_ROOT="$2"; shift 2 ;;
    --download-root) DOWNLOAD_ROOT="$2"; shift 2 ;;
    --poll-seconds) POLL_SECONDS="$2"; shift 2 ;;
    --run-baseline) RUN_BASELINE="$(to_bool "$2")"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

MANIFEST_PATH="${COHORT_ROOT}/extract_full/extraction_manifest.csv"
echo "[info] Waiting for manifest: ${MANIFEST_PATH}"
echo "[info] Poll interval: ${POLL_SECONDS}s"

while [[ ! -s "${MANIFEST_PATH}" ]]; do
  sleep "${POLL_SECONDS}"
done

echo "[info] Detected full extraction manifest. Launching postprocess..."
"${SCRIPT_DIR}/run_stage_d_full_multiframe_postprocess.sh" \
  --cohort-root "${COHORT_ROOT}" \
  --download-root "${DOWNLOAD_ROOT}" \
  --run-baseline "${RUN_BASELINE}"

echo "[done] Watcher completed."
