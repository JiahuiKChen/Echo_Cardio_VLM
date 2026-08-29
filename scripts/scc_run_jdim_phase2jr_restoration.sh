#!/usr/bin/env bash
set -euo pipefail
umask 077

STAGE="${1:-}"
MODE="${2:-run}"
if [[ "${STAGE}" != "A2" && "${STAGE}" != "A3" ]]; then
  echo "[error] Usage: scripts/scc_run_jdim_phase2jr_restoration.sh A2|A3 [preflight|run]" >&2
  exit 2
fi
if [[ "${MODE}" != "preflight" && "${MODE}" != "run" ]]; then
  echo "[error] Usage: scripts/scc_run_jdim_phase2jr_restoration.sh A2|A3 [preflight|run]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/jdim_phase2jr_scc_common.sh
source "${SCRIPT_DIR}/jdim_phase2jr_scc_common.sh"

phase2jr_common_preflight yes
cd "${PHASE2JR_REPO}"

if [[ "${MODE}" == "preflight" ]]; then
  echo "PHASE2JR_${STAGE}_PREFLIGHT_OK"
  exit 0
fi

if [[ "${STAGE}" == "A2" ]]; then
  "${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py resume-restoration \
    --locked-url-list "${PHASE2JR_LOCKED_URL_LIST}" \
    --restoration-manifest-csv "${PHASE2JR_RESTORATION_MANIFEST}" \
    --source-destination-root "${PHASE2JR_SOURCE_DESTINATION}" \
    --source-commit "${PHASE2JR_SOURCE_COMMIT}" \
    --run-root "${PHASE2JR_A2_ROOT}" \
    --netrc-path "${HOME}/.netrc" \
    --continuation-label A2 \
    --max-workers 2 \
    --checkpoint-every 25 \
    --checkpoint-seconds 900 \
    --soft-stop-seconds 38700 \
    --incomplete-status RESTORATION_INCOMPLETE_RESUMABLE
  STATUS="$("${PHASE2JR_PY}" -c "import json; print(json.load(open('${PHASE2JR_A2_ROOT}/aggregate_safe/phase2jr_restoration_certificate.json'))['status'])")"
  case "${STATUS}" in
    LOCKED_ROSTER_SOURCE_RESTORED|RESTORATION_INCOMPLETE_RESUMABLE)
      echo "${STATUS}"
      ;;
    *)
      echo "[error] ${STATUS}" >&2
      exit 2
      ;;
  esac
  exit 0
fi

A2_CERT="${PHASE2JR_A2_ROOT}/aggregate_safe/phase2jr_restoration_certificate.json"
A2_RESULTS="${PHASE2JR_A2_ROOT}/restricted/restoration/source_restoration_results_restricted.csv"
if [[ ! -f "${A2_CERT}" || ! -f "${A2_RESULTS}" ]]; then
  echo "[error] BLOCKED_RESTORATION_CONTINUATION_STATE" >&2
  exit 2
fi
A2_STATUS="$("${PHASE2JR_PY}" -c "import json; print(json.load(open('${A2_CERT}')).get('status',''))")"
if [[ "${A2_STATUS}" == "LOCKED_ROSTER_SOURCE_RESTORED" ]]; then
  "${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py verify-restoration \
    --certificate "${A2_CERT}" \
    --results-csv "${A2_RESULTS}" \
    --expected-source-commit "${PHASE2JR_SOURCE_COMMIT}" \
    --expected-url-list-sha256 "${PHASE2JR_LOCKED_URL_SHA256}" \
    --safe-output-json "${PHASE2JR_A3_ROOT}/aggregate_safe/phase2jr_restoration_certificate.json"
  echo "LOCKED_ROSTER_SOURCE_RESTORED"
  exit 0
fi
if [[ "${A2_STATUS}" != "RESTORATION_INCOMPLETE_RESUMABLE" ]]; then
  echo "[error] BLOCKED_RESTORATION_CONTINUATION_STATE" >&2
  exit 2
fi

"${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py verify-restoration-continuation \
  --certificate "${A2_CERT}" \
  --results-csv "${A2_RESULTS}" \
  --expected-source-commit "${PHASE2JR_SOURCE_COMMIT}" \
  --expected-url-list-sha256 "${PHASE2JR_LOCKED_URL_SHA256}" \
  --expected-destination-root "${PHASE2JR_SOURCE_DESTINATION}"
"${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py resume-restoration \
  --locked-url-list "${PHASE2JR_LOCKED_URL_LIST}" \
  --restoration-manifest-csv "${PHASE2JR_RESTORATION_MANIFEST}" \
  --source-destination-root "${PHASE2JR_SOURCE_DESTINATION}" \
  --source-commit "${PHASE2JR_SOURCE_COMMIT}" \
  --run-root "${PHASE2JR_A3_ROOT}" \
  --netrc-path "${HOME}/.netrc" \
  --continuation-label A3 \
  --max-workers 2 \
  --checkpoint-every 25 \
  --checkpoint-seconds 900 \
  --soft-stop-seconds 38700 \
  --incomplete-status BLOCKED_LOCKED_SOURCE_RESTORATION
A3_STATUS="$("${PHASE2JR_PY}" -c "import json; print(json.load(open('${PHASE2JR_A3_ROOT}/aggregate_safe/phase2jr_restoration_certificate.json'))['status'])")"
case "${A3_STATUS}" in
  LOCKED_ROSTER_SOURCE_RESTORED|BLOCKED_LOCKED_SOURCE_RESTORATION)
    echo "${A3_STATUS}"
    ;;
  *)
    echo "[error] ${A3_STATUS}" >&2
    exit 2
    ;;
esac
