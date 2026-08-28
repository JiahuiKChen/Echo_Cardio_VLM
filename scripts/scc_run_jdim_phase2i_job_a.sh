#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
if [[ "${MODE}" != "run" && "${MODE}" != "preflight" ]]; then
  echo "[error] Usage: scripts/scc_run_jdim_phase2i_job_a.sh [preflight|run]" >&2
  exit 2
fi

REPO="${JDIM_REPO_ROOT:-/restricted/project/mimicecho/code/Echo_Cardio_VLM_jdim_phase2er_driver_fix}"
PY="${JDIM_PYTHON_BIN:-/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python}"
SOURCE_COMMIT="${JDIM_PHASE2I_SOURCE_COMMIT:-}"
AUTHORIZED_BASE_COMMIT="58e9de15fba97a7f2126703a1eb6b52d95ff9c63"
OUT="${JDIM_PHASE2I_OUTPUT_ROOT:-/restricted/project/mimicecho/outputs/jdim_phase2j_restoration_v2}"
EXPECTED_OUT="/restricted/project/mimicecho/outputs/jdim_phase2j_restoration_v2"
RESTORED_SOURCE="/restricted/project/mimicecho/outputs/jdim_phase2i_official_source_v1"
AUDIT_ROOT="/restricted/project/mimicecho/outputs/jdim_audit_roster_pilot_v1"
CANONICAL_CERT="/restricted/project/mimicecho/outputs/jdim_phase2f_finalizer_v1/aggregate_safe/phase2e_corrected_outputs_canonical_certificate.json"
COHORT_ROOT="/restricted/project/mimicecho/outputs/jdim_cohort_audit_prep_v2/aggregate_safe/cohort_flow"

LOCK="${AUDIT_ROOT}/aggregate_safe/input_content_audit/locked_roster/audit_roster_lock.json"
SAMPLING_SUMMARY="${AUDIT_ROOT}/aggregate_safe/input_content_audit/locked_roster/audit_sampling_summary.json"
AVAILABILITY="${AUDIT_ROOT}/aggregate_safe/input_content_audit/source_availability/source_availability_summary.json"
CLIP_ROSTER="${AUDIT_ROOT}/restricted/input_content_audit/locked_roster/canonical_clip_roster_restricted.csv"
AUDIT_LINKAGE="${AUDIT_ROOT}/restricted/input_content_audit/locked_roster/audit_linkage.csv"
SAMPLING_DESIGN="${AUDIT_ROOT}/restricted/input_content_audit/locked_roster/sampling_design_restricted.csv"
RESTORATION_MANIFEST="${AUDIT_ROOT}/restricted/input_content_audit/source_availability/source_restoration_manifest.csv"
PILOT_ROWS="${AUDIT_ROOT}/restricted/input_content_audit/technical_pilot/reconstruction_pilot_rows.csv"
LOCKED_URL_LIST="/restricted/project/mimicecho/outputs/jdim_phase2i_restoration_v1/restricted/restoration/locked_official_source_urls.txt"

require_file() {
  if [[ ! -f "$1" ]]; then
    echo "[error] Required file is missing: $1" >&2
    exit 2
  fi
}

verify_sha256() {
  local observed
  observed="$(sha256sum "$1" | awk '{print $1}')"
  if [[ "${observed}" != "$2" ]]; then
    echo "[error] Locked artifact hash mismatch: $1" >&2
    exit 2
  fi
}

if [[ -z "${SOURCE_COMMIT}" ]]; then
  echo "[error] JDIM_PHASE2I_SOURCE_COMMIT is required" >&2
  exit 2
fi
if [[ "${OUT}" != "${EXPECTED_OUT}" ]]; then
  echo "[error] JDIM_PHASE2I_OUTPUT_ROOT differs from the authorized Phase 2J root" >&2
  exit 2
fi
for path in \
  "${PY}" \
  "${CANONICAL_CERT}" \
  "${COHORT_ROOT}/cohort_flow_lock.json" \
  "${COHORT_ROOT}/cohort_flow_summary.json" \
  "${COHORT_ROOT}/cohort_flow_invariants.json" \
  "${LOCK}" \
  "${SAMPLING_SUMMARY}" \
  "${AVAILABILITY}" \
  "${CLIP_ROSTER}" \
  "${AUDIT_LINKAGE}" \
  "${SAMPLING_DESIGN}" \
  "${RESTORATION_MANIFEST}" \
  "${PILOT_ROWS}" \
  "${LOCKED_URL_LIST}"; do
  require_file "${path}"
done

verify_sha256 "${CANONICAL_CERT}" "bbd38f8f913c74ab97d005e833a470cf55f25cc0c18e0063854ea5b6528426a2"
verify_sha256 "${COHORT_ROOT}/cohort_flow_lock.json" "fb528a9611cf512b305c431ceffd705b6680ca9a24dadc14080731786933f233"
verify_sha256 "${COHORT_ROOT}/cohort_flow_summary.json" "aa8bda7991e341cfdc5cfb84407c3005bb01062258211f886d1c98c53d8014c8"
verify_sha256 "${COHORT_ROOT}/cohort_flow_invariants.json" "13a0fdfe06c92e441c6e0c30c1ed4722e94954e4869399daff4333a261ab45fa"
verify_sha256 "${LOCK}" "5c9d4ac72b62c6bec6fa09735535c0b2625f8de09572e7006dd91c46df5019ef"
verify_sha256 "${SAMPLING_SUMMARY}" "272e755825b793e376825d9eddb795876dfdb60c41b2ec641a12758be1a58607"
verify_sha256 "${AVAILABILITY}" "89f4d787168b118a6e73b03d0698015bfc93ccf2a5f92b97ab1f1172a57a121d"
verify_sha256 "${CLIP_ROSTER}" "4220c60eacc201c9de4193719fc79b4cd78289909f7169ba618ecbb8af52c0eb"
verify_sha256 "${AUDIT_LINKAGE}" "94d5142bf44045903f008f614d8ae24999d93d1700054bac9b5c4f0aa094b667"
verify_sha256 "${SAMPLING_DESIGN}" "99a30d5c4de4462d60636dc1f16a949ccd7393f2634dddb8b02287fe4a464dc0"
verify_sha256 "${RESTORATION_MANIFEST}" "1340fbe77e36f9c47b29f551fa399037f24d88e2af631da7eab009812eedfe3c"
verify_sha256 "${PILOT_ROWS}" "acf7040135d3ce892dd0164750f65ede1c035585e7f7c1869fead258662d6356"
verify_sha256 "${LOCKED_URL_LIST}" "faec57d91dd054fbcc553d8ae92993b915ed1b25fed02fcc59bdd1bf1685ba21"
if [[ "$(wc -l < "${LOCKED_URL_LIST}" | tr -d ' ')" != "4808" ]]; then
  echo "[error] locked official-source URL count changed" >&2
  exit 2
fi

if [[ "$(git -C "${REPO}" rev-parse HEAD)" != "${SOURCE_COMMIT}" ]]; then
  echo "[error] SCC checkout is not at JDIM_PHASE2I_SOURCE_COMMIT" >&2
  exit 2
fi
if ! git -C "${REPO}" merge-base --is-ancestor "${AUTHORIZED_BASE_COMMIT}" "${SOURCE_COMMIT}" \
  || [[ "$(git -C "${REPO}" rev-list --count "${AUTHORIZED_BASE_COMMIT}..${SOURCE_COMMIT}")" != "1" ]]; then
  echo "[error] source is not the single authorized operational repair on the Phase 2J base" >&2
  exit 2
fi
if [[ -n "$(git -C "${REPO}" status --porcelain)" ]]; then
  echo "[error] SCC checkout is not clean" >&2
  exit 2
fi
if [[ ! -f "${HOME}/.netrc" || "$(stat -c '%a' "${HOME}/.netrc")" != "600" ]]; then
  echo "[error] BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION" >&2
  exit 2
fi
if ! wget --quiet --spider --netrc --https-only --max-redirect=0 \
  "https://physionet.org/files/mimic-iv-echo/1.0/"; then
  echo "[error] BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION" >&2
  exit 2
fi

cd "${REPO}"
"${PY}" scripts/run_jdim_phase2i.py verify-locked \
  --roster-lock-json "${LOCK}" \
  --source-availability-json "${AVAILABILITY}"
"${PY}" -m py_compile \
  scripts/jdim_tier1/phase2i.py \
  scripts/jdim_tier1/audit_interface.py \
  scripts/run_jdim_phase2i.py \
  scripts/serve_jdim_audit_interface.py
if [[ "${MODE}" == "preflight" ]]; then
  if [[ -e "${OUT}" ]]; then
    echo "[error] Phase 2I Job A output root already exists" >&2
    exit 2
  fi
  echo "PHASE2I_JOB_A_PREFLIGHT_OK"
  exit 0
fi

if [[ -e "${OUT}" ]]; then
  echo "[error] refusing to overwrite immutable Phase 2I Job A output root" >&2
  exit 2
fi
mkdir -p "${OUT}/restricted" "${OUT}/aggregate_safe"

"${PY}" scripts/run_jdim_phase2i.py diagnose-pilot \
  --pilot-rows-csv "${PILOT_ROWS}" \
  --clip-roster-csv "${CLIP_ROSTER}" \
  --restricted-output-root "${OUT}/restricted/pilot_diagnosis" \
  --safe-output-dir "${OUT}/aggregate_safe/pilot_diagnosis" \
  --expected-attempts 18

DIAGNOSIS="${OUT}/aggregate_safe/pilot_diagnosis/pilot_replay_summary.json"
DIAGNOSIS_STATUS="$("${PY}" -c "import json; print(json.load(open('${DIAGNOSIS}'))['status'])")"
echo "${DIAGNOSIS_STATUS}"
if [[ "${DIAGNOSIS_STATUS}" != "REPLAY_PATH_VALIDATED" && "${DIAGNOSIS_STATUS}" != "REPLAY_USABLE_WITH_TIERED_REPORTING" ]]; then
  exit 0
fi

RESTORE_RESTRICTED="${OUT}/restricted/restoration"
RESTORE_SAFE="${OUT}/aggregate_safe/restoration"
JOB_A_COMMAND="$(printf '%s\n' \
  "export JDIM_PHASE2I_SOURCE_COMMIT=${SOURCE_COMMIT}" \
  "export JDIM_PHASE2I_OUTPUT_ROOT=${OUT}" \
  "${REPO}/scripts/scc_run_jdim_phase2i_job_a.sh preflight" \
  "mkdir -p /restricted/project/mimicecho/outputs/jdim_phase2j_scheduler_logs_v1" \
  "qsub -cwd -v JDIM_PHASE2I_SOURCE_COMMIT=${SOURCE_COMMIT},JDIM_PHASE2I_OUTPUT_ROOT=${OUT} -P mimicecho -N jdim_phase2j_a -j y -o /restricted/project/mimicecho/outputs/jdim_phase2j_scheduler_logs_v1 -l h_rt=12:00:00 -pe omp 2 -l mem_per_core=8G -b y ${REPO}/scripts/scc_run_jdim_phase2i_job_a.sh run")"
"${PY}" scripts/run_jdim_phase2i.py restore-sources \
  --restoration-manifest-csv "${RESTORATION_MANIFEST}" \
  --restricted-output-root "${RESTORE_RESTRICTED}" \
  --safe-output-dir "${RESTORE_SAFE}" \
  --source-destination-root "${RESTORED_SOURCE}" \
  --netrc-path "${HOME}/.netrc" \
  --max-workers 4 \
  --job-a-command "${JOB_A_COMMAND}"

RESTORATION_STATUS="$("${PY}" -c "import json; print(json.load(open('${RESTORE_SAFE}/source_restoration_summary.json'))['status'])")"
echo "${RESTORATION_STATUS}"
verify_sha256 "${RESTORE_RESTRICTED}/locked_official_source_urls.txt" "faec57d91dd054fbcc553d8ae92993b915ed1b25fed02fcc59bdd1bf1685ba21"
if [[ "${RESTORATION_STATUS}" != "LOCKED_ROSTER_SOURCE_RESTORED" ]]; then
  exit 0
fi

"${PY}" scripts/run_jdim_phase2i.py build-inventory \
  --clip-roster-csv "${CLIP_ROSTER}" \
  --audit-linkage-csv "${AUDIT_LINKAGE}" \
  --restored-source-results-csv "${RESTORE_RESTRICTED}/source_restoration_results_restricted.csv" \
  --restricted-output-root "${OUT}/restricted/technical_inventory" \
  --safe-output-dir "${OUT}/aggregate_safe/technical_inventory"

TECHNICAL_STATUS="$("${PY}" -c "import json; print(json.load(open('${OUT}/aggregate_safe/technical_inventory/technical_input_inventory_summary.json'))['status'])")"
echo "${TECHNICAL_STATUS}"
if [[ "${TECHNICAL_STATUS}" == "AUDIT_INPUTS_TECHNICALLY_LOCKED" ]]; then
  "${PY}" -c "import hashlib,json,pathlib; root=pathlib.Path('${OUT}'); restoration_path=root/'aggregate_safe/restoration/source_restoration_summary.json'; technical_path=root/'aggregate_safe/technical_inventory/technical_input_inventory_summary.json'; digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(); payload={'status':'AUDIT_INPUTS_TECHNICALLY_LOCKED','source_commit':'${SOURCE_COMMIT}','restoration':json.load(open(restoration_path)),'technical_inventory':json.load(open(technical_path)),'restoration_certificate_sha256':digest(restoration_path),'technical_lock_sha256':digest(technical_path)}; out=root/'aggregate_safe/phase2j_job_a_certificate.json'; out.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')"
fi
exit 0
