#!/usr/bin/env bash
set -euo pipefail
umask 077

STAGE="${1:-}"
MODE="${2:-run}"
if [[ "${STAGE}" != "B1" && "${STAGE}" != "B2" ]]; then
  echo "[error] Usage: scripts/scc_run_jdim_phase2jr_interface.sh B1|B2 [preflight|run]" >&2
  exit 2
fi
if [[ "${MODE}" != "preflight" && "${MODE}" != "run" ]]; then
  echo "[error] Usage: scripts/scc_run_jdim_phase2jr_interface.sh B1|B2 [preflight|run]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/jdim_phase2jr_scc_common.sh
source "${SCRIPT_DIR}/jdim_phase2jr_scc_common.sh"

phase2jr_common_preflight no
cd "${PHASE2JR_REPO}"
if [[ "${MODE}" == "preflight" ]]; then
  echo "PHASE2JR_${STAGE}_PREFLIGHT_OK"
  exit 0
fi

RESTORATION_ROOT="${JDIM_PHASE2JR_DIRECT_RESTORATION_ROOT:-}"
if [[ -z "${RESTORATION_ROOT}" ]]; then
  A3_CERT="${PHASE2JR_A3_ROOT}/aggregate_safe/phase2jr_restoration_certificate.json"
  if [[ ! -f "${A3_CERT}" ]]; then
    echo "[error] BLOCKED_LOCKED_SOURCE_RESTORATION" >&2
    exit 2
  fi
  A3_STATUS="$("${PHASE2JR_PY}" -c "import json; print(json.load(open('${A3_CERT}')).get('status',''))")"
  A3_NOOP="$("${PHASE2JR_PY}" -c "import json; print(str(bool(json.load(open('${A3_CERT}')).get('no_op_validation',False))).lower())")"
  if [[ "${A3_STATUS}" != "LOCKED_ROSTER_SOURCE_RESTORED" ]]; then
    echo "[error] BLOCKED_LOCKED_SOURCE_RESTORATION" >&2
    exit 2
  fi
  if [[ "${A3_NOOP}" == "true" ]]; then
    RESTORATION_ROOT="${PHASE2JR_A2_ROOT}"
  else
    RESTORATION_ROOT="${PHASE2JR_A3_ROOT}"
  fi
fi
if [[ "${RESTORATION_ROOT}" != "${PHASE2JR_A2_ROOT}" && "${RESTORATION_ROOT}" != "${PHASE2JR_A3_ROOT}" ]]; then
  echo "[error] BLOCKED_LOCKED_SOURCE_RESTORATION" >&2
  exit 2
fi
RESTORATION_CERT="${RESTORATION_ROOT}/aggregate_safe/phase2jr_restoration_certificate.json"
RESTORATION_RESULTS="${RESTORATION_ROOT}/restricted/restoration/source_restoration_results_restricted.csv"
if [[ ! -f "${RESTORATION_CERT}" || ! -f "${RESTORATION_RESULTS}" ]]; then
  echo "[error] BLOCKED_LOCKED_SOURCE_RESTORATION" >&2
  exit 2
fi
RESTORATION_STATUS="$("${PHASE2JR_PY}" -c "import json; print(json.load(open('${RESTORATION_CERT}')).get('status',''))")"
if [[ "${STAGE}" == "B1" ]]; then
  phase2jr_verify_upstream_accounting \
    "${JDIM_PHASE2JR_UPSTREAM_JOB_ID:-}" \
    "${RESTORATION_STATUS}"
fi
"${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py verify-restoration \
  --certificate "${RESTORATION_CERT}" \
  --results-csv "${RESTORATION_RESULTS}" \
  --expected-source-commit "${PHASE2JR_SOURCE_COMMIT}" \
  --expected-url-list-sha256 "${PHASE2JR_LOCKED_URL_SHA256}"

RUN_ROOT="${PHASE2JR_B1_ROOT}"
INCOMPLETE_STATUS="INTERFACE_INCOMPLETE_RESUMABLE"
if [[ "${STAGE}" == "B2" ]]; then
  RUN_ROOT="${PHASE2JR_B2_ROOT}"
  INCOMPLETE_STATUS="BLOCKED_AUDIT_INTERFACE"
  B1_CERT="${PHASE2JR_B1_ROOT}/aggregate_safe/phase2jr_interface_run_certificate.json"
  if [[ ! -f "${B1_CERT}" ]]; then
    B1_CERT="${PHASE2JR_B1_ROOT}/aggregate_safe/interface_checkpoint.json"
    if [[ ! -f "${B1_CERT}" ]]; then
      echo "[error] BLOCKED_AUDIT_INTERFACE" >&2
      exit 2
    fi
  fi
  B1_STATUS="$("${PHASE2JR_PY}" -c "import json; print(json.load(open('${B1_CERT}')).get('status',''))")"
  phase2jr_verify_upstream_accounting \
    "${JDIM_PHASE2JR_UPSTREAM_JOB_ID:-}" \
    "${B1_STATUS}"
  if [[ "${B1_STATUS}" == "INTERFACE_INCOMPLETE_RESUMABLE" ]]; then
    "${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py verify-interface-continuation \
      --certificate "${B1_CERT}" \
      --expected-source-commit "${PHASE2JR_SOURCE_COMMIT}"
  elif [[ "${B1_STATUS}" != "READY_FOR_BLINDED_HUMAN_AUDIT" ]]; then
    echo "[error] BLOCKED_AUDIT_INTERFACE" >&2
    exit 2
  fi
fi

"${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py continue-interface \
  --persistent-root "${PHASE2JR_INTERFACE_ROOT}" \
  --run-root "${RUN_ROOT}" \
  --restored-source-results-csv "${RESTORATION_RESULTS}" \
  --clip-roster-csv "${PHASE2JR_CLIP_ROSTER}" \
  --audit-linkage-csv "${PHASE2JR_AUDIT_LINKAGE}" \
  --primary-reader-manifest-csv "${PHASE2JR_PRIMARY}" \
  --second-reader-manifest-csv "${PHASE2JR_SECOND}" \
  --source-commit "${PHASE2JR_SOURCE_COMMIT}" \
  --checkpoint-every 25 \
  --checkpoint-seconds 900 \
  --soft-stop-seconds 38700 \
  --incomplete-status "${INCOMPLETE_STATUS}"

STATUS="$("${PHASE2JR_PY}" -c "import json; print(json.load(open('${RUN_ROOT}/aggregate_safe/phase2jr_interface_run_certificate.json'))['status'])")"
if [[ "${STAGE}" == "B1" ]]; then
  case "${STATUS}" in
    READY_FOR_BLINDED_HUMAN_AUDIT|INTERFACE_INCOMPLETE_RESUMABLE)
      echo "${STATUS}"
      ;;
    *)
      echo "[error] ${STATUS}" >&2
      exit 2
      ;;
  esac
else
  case "${STATUS}" in
    READY_FOR_BLINDED_HUMAN_AUDIT)
      echo "${STATUS}"
      ;;
    BLOCKED_AUDIT_INTERFACE)
      echo "[error] ${STATUS}" >&2
      exit 2
      ;;
    *)
      echo "[error] ${STATUS}" >&2
      exit 2
      ;;
  esac
fi
