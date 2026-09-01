#!/usr/bin/env bash
set -euo pipefail

REPO="${JDIM_REPO_ROOT:-/restricted/project/mimicecho/code/Echo_Cardio_VLM_jdim_phase2er_driver_fix}"
PY="${JDIM_PYTHON_BIN:-/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python}"
SOURCE_COMMIT="${JDIM_REDUCED_AUDIT_SOURCE_COMMIT:-}"
LOG_ROOT="/restricted/project/mimicecho/outputs/jdim_reduced_audit_scheduler_logs_v1"

if [[ -z "${SOURCE_COMMIT}" ]]; then
  echo "[error] JDIM_REDUCED_AUDIT_SOURCE_COMMIT is required" >&2
  exit 2
fi
cd "${REPO}"
"${PY}" -m unittest discover -s tests -p 'test_*.py' -q
scripts/scc_run_jdim_reduced_audit.sh preflight
mkdir -p "${LOG_ROOT}"

qsub \
  -N jdim_reduced_audit \
  -pe omp 1 \
  -l h_rt=01:00:00 \
  -l mem_per_core=4G \
  -j y \
  -o "${LOG_ROOT}" \
  -v "JDIM_REDUCED_AUDIT_SOURCE_COMMIT=${SOURCE_COMMIT}" \
  "${REPO}/scripts/scc_run_jdim_reduced_audit.sh" run
