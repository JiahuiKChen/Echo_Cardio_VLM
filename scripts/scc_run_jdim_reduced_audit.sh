#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
if [[ "${MODE}" != "preflight" && "${MODE}" != "run" ]]; then
  echo "[error] Usage: scripts/scc_run_jdim_reduced_audit.sh [preflight|run]" >&2
  exit 2
fi

REPO="${JDIM_REPO_ROOT:-/restricted/project/mimicecho/code/Echo_Cardio_VLM_jdim_phase2er_driver_fix}"
PY="${JDIM_PYTHON_BIN:-/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python}"
SOURCE_COMMIT="${JDIM_REDUCED_AUDIT_SOURCE_COMMIT:-}"
AUTHORIZED_PARENT="4e0ec3248b88ed8286de3a91b589627bb2da09b6"
OUTPUT_ROOT="/restricted/project/mimicecho/outputs/jdim_reduced_audit_15_per_target_v1"

if [[ -z "${SOURCE_COMMIT}" ]]; then
  echo "[error] JDIM_REDUCED_AUDIT_SOURCE_COMMIT is required" >&2
  exit 2
fi
if [[ "$(git -C "${REPO}" rev-parse HEAD)" != "${SOURCE_COMMIT}" ]]; then
  echo "[error] SCC checkout is not at the authorized source commit" >&2
  exit 2
fi
if ! git -C "${REPO}" merge-base --is-ancestor "${AUTHORIZED_PARENT}" "${SOURCE_COMMIT}"; then
  echo "[error] authorized parent is not an ancestor of the source commit" >&2
  exit 2
fi
if [[ "$(git -C "${REPO}" rev-list --count "${AUTHORIZED_PARENT}..${SOURCE_COMMIT}")" != "1" ]]; then
  echo "[error] source must be one minimal commit above the authorized parent" >&2
  exit 2
fi
if [[ -n "$(git -C "${REPO}" status --porcelain)" ]]; then
  echo "[error] SCC checkout is not clean" >&2
  exit 2
fi

cd "${REPO}"
"${PY}" -m py_compile \
  scripts/jdim_tier1/reduced_audit.py \
  scripts/jdim_tier1/reduced_audit_interface.py \
  scripts/prepare_jdim_reduced_audit.py \
  scripts/serve_jdim_reduced_audit.py
"${PY}" scripts/prepare_jdim_reduced_audit.py preflight

if [[ "${MODE}" == "preflight" ]]; then
  if [[ -e "${OUTPUT_ROOT}" ]]; then
    echo "[error] immutable reduced-audit output root already exists" >&2
    exit 2
  fi
  echo "REDUCED_AUDIT_PREFLIGHT_PASSED"
  exit 0
fi

if [[ -e "${OUTPUT_ROOT}" ]]; then
  echo "[error] refusing to overwrite reduced-audit output root" >&2
  exit 2
fi
"${PY}" scripts/prepare_jdim_reduced_audit.py build \
  --output-root "${OUTPUT_ROOT}" \
  --source-commit "${SOURCE_COMMIT}"
"${PY}" scripts/prepare_jdim_reduced_audit.py validate \
  --output-root "${OUTPUT_ROOT}"

STATUS="$("${PY}" -c "import json; print(json.load(open('${OUTPUT_ROOT}/aggregate_safe/reduced_audit_ready_certificate.json'))['status'])")"
if [[ "${STATUS}" != "READY_FOR_REDUCED_BLINDED_HUMAN_AUDIT" ]]; then
  echo "[error] reduced role-aware interface did not reach readiness" >&2
  exit 2
fi
echo "${STATUS}"
