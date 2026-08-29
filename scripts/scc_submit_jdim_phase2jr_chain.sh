#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/jdim_phase2jr_scc_common.sh
source "${SCRIPT_DIR}/jdim_phase2jr_scc_common.sh"

phase2jr_common_preflight yes
cd "${PHASE2JR_REPO}"

"${PHASE2JR_REPO}/scripts/scc_run_jdim_phase2jr_restoration.sh" A2 preflight
"${PHASE2JR_REPO}/scripts/scc_run_jdim_phase2jr_restoration.sh" A3 preflight
"${PHASE2JR_REPO}/scripts/scc_run_jdim_phase2jr_interface.sh" B1 preflight
"${PHASE2JR_REPO}/scripts/scc_run_jdim_phase2jr_interface.sh" B2 preflight
"${PHASE2JR_PY}" -m unittest discover -s tests -p 'test_*.py' -q

ACCOUNTING="$(qacct -j 7354017)"
printf '%s\n' "${ACCOUNTING}" | "${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py verify-qacct \
  --expected-jobnumber 7354017 \
  --expected-failed 100 \
  --expected-exit-status 137 \
  --expected-ru-wallclock 43201
for absent in \
  "${PHASE2JR_OLD_ROOT}/aggregate_safe/phase2j_job_a_certificate.json" \
  "${PHASE2JR_OLD_ROOT}/aggregate_safe/restoration/source_restoration_summary.json" \
  "${PHASE2JR_OLD_ROOT}/aggregate_safe/technical_inventory/technical_input_inventory_summary.json"; do
  if [[ -e "${absent}" ]]; then
    echo "[error] prior wall-time run unexpectedly contains a completion artifact" >&2
    exit 2
  fi
done

for new_root in \
  "${PHASE2JR_A2_ROOT}" \
  "${PHASE2JR_A3_ROOT}" \
  "${PHASE2JR_INTERFACE_ROOT}" \
  "${PHASE2JR_LOG_ROOT}"; do
  if [[ -e "${new_root}" ]]; then
    echo "[error] authorized Phase 2J-R output root already exists" >&2
    exit 2
  fi
done

"${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py assess-restoration \
  --locked-url-list "${PHASE2JR_LOCKED_URL_LIST}" \
  --restoration-manifest-csv "${PHASE2JR_RESTORATION_MANIFEST}" \
  --source-destination-root "${PHASE2JR_SOURCE_DESTINATION}" \
  --source-commit "${PHASE2JR_SOURCE_COMMIT}" \
  --no-write \
  --require-phase2jr2-current-state

mkdir -p \
  "${PHASE2JR_A2_ROOT}/restricted/preflight" \
  "${PHASE2JR_A2_ROOT}/aggregate_safe/preflight" \
  "${PHASE2JR_LOG_ROOT}"
chmod 700 "${PHASE2JR_A2_ROOT}" "${PHASE2JR_LOG_ROOT}"
"${PHASE2JR_PY}" scripts/run_jdim_phase2jr.py assess-restoration \
  --locked-url-list "${PHASE2JR_LOCKED_URL_LIST}" \
  --restoration-manifest-csv "${PHASE2JR_RESTORATION_MANIFEST}" \
  --source-destination-root "${PHASE2JR_SOURCE_DESTINATION}" \
  --source-commit "${PHASE2JR_SOURCE_COMMIT}" \
  --restricted-output-csv "${PHASE2JR_A2_ROOT}/restricted/preflight/restoration_state_restricted.csv" \
  --safe-output-json "${PHASE2JR_A2_ROOT}/aggregate_safe/preflight/restoration_state_summary.json" \
  --require-phase2jr2-current-state
STATE_STATUS="RESTORATION_STATE_RESUMABLE"

QSUB_COMMON=(
  -terse
  -cwd
  -P mimicecho
  -j y
  -o "${PHASE2JR_LOG_ROOT}"
  -l h_rt=11:30:00
  -pe omp 2
  -l mem_per_core=8G
)
SOURCE_ENV="JDIM_PHASE2JR_SOURCE_COMMIT=${PHASE2JR_SOURCE_COMMIT}"

A2_JOB="$(qsub "${QSUB_COMMON[@]}" -N jdim_p2jr_a2 -v "${SOURCE_ENV}" -b y \
  "${PHASE2JR_REPO}/scripts/scc_run_jdim_phase2jr_restoration.sh" A2 run)"
A3_ENV="${SOURCE_ENV},JDIM_PHASE2JR_UPSTREAM_JOB_ID=${A2_JOB}"
A3_JOB="$(qsub "${QSUB_COMMON[@]}" -N jdim_p2jr_a3 -hold_jid "${A2_JOB}" -v "${A3_ENV}" -b y \
  "${PHASE2JR_REPO}/scripts/scc_run_jdim_phase2jr_restoration.sh" A3 run)"
B1_ENV="${SOURCE_ENV},JDIM_PHASE2JR_UPSTREAM_JOB_ID=${A3_JOB}"
B1_JOB="$(qsub "${QSUB_COMMON[@]}" -N jdim_p2jr_b1 -hold_jid "${A3_JOB}" -v "${B1_ENV}" -b y \
  "${PHASE2JR_REPO}/scripts/scc_run_jdim_phase2jr_interface.sh" B1 run)"
B2_ENV="${SOURCE_ENV},JDIM_PHASE2JR_UPSTREAM_JOB_ID=${B1_JOB}"
B2_JOB="$(qsub "${QSUB_COMMON[@]}" -N jdim_p2jr_b2 -hold_jid "${B1_JOB}" -v "${B2_ENV}" -b y \
  "${PHASE2JR_REPO}/scripts/scc_run_jdim_phase2jr_interface.sh" B2 run)"
printf 'STATE_STATUS=%s\nA2_JOB=%s\nA3_JOB=%s\nB1_JOB=%s\nB2_JOB=%s\nDEPENDENCIES=A2->A3->B1->B2\n' \
  "${STATE_STATUS}" "${A2_JOB}" "${A3_JOB}" "${B1_JOB}" "${B2_JOB}"
