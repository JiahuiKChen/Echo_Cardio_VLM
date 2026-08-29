#!/usr/bin/env bash

PHASE2JR_REPO="${JDIM_REPO_ROOT:-/restricted/project/mimicecho/code/Echo_Cardio_VLM_jdim_phase2er_driver_fix}"
PHASE2JR_PY="${JDIM_PYTHON_BIN:-/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python}"
PHASE2JR_SOURCE_COMMIT="${JDIM_PHASE2JR_SOURCE_COMMIT:-}"
PHASE2JR_AUTHORIZED_PARENT="229a3a88b040eb8728e7f1e73a43711a704c4d7f"

PHASE2JR_EVIDENCE_ROOT="/restricted/project/mimicecho/outputs/jdim_phase2j_restoration_v3a"
PHASE2JR_A2_ROOT="/restricted/project/mimicecho/outputs/jdim_phase2j_restoration_v3b"
PHASE2JR_A3_ROOT="/restricted/project/mimicecho/outputs/jdim_phase2j_restoration_v3c"
PHASE2JR_SOURCE_DESTINATION="/restricted/project/mimicecho/outputs/jdim_phase2i_official_source_v1"
PHASE2JR_INTERFACE_ROOT="/restricted/project/mimicecho/outputs/jdim_phase2j_audit_interface_v2"
PHASE2JR_B1_ROOT="${PHASE2JR_INTERFACE_ROOT}/runs/b1"
PHASE2JR_B2_ROOT="${PHASE2JR_INTERFACE_ROOT}/runs/b2"
PHASE2JR_OLD_ROOT="/restricted/project/mimicecho/outputs/jdim_phase2j_restoration_v2"
PHASE2JR_LOG_ROOT="/restricted/project/mimicecho/outputs/jdim_phase2jr2_scheduler_logs_v1"

PHASE2JR_AUDIT_ROOT="/restricted/project/mimicecho/outputs/jdim_audit_roster_pilot_v1"
PHASE2JR_CANONICAL_CERT="/restricted/project/mimicecho/outputs/jdim_phase2f_finalizer_v1/aggregate_safe/phase2e_corrected_outputs_canonical_certificate.json"
PHASE2JR_COHORT_ROOT="/restricted/project/mimicecho/outputs/jdim_cohort_audit_prep_v2/aggregate_safe/cohort_flow"
PHASE2JR_LOCK="${PHASE2JR_AUDIT_ROOT}/aggregate_safe/input_content_audit/locked_roster/audit_roster_lock.json"
PHASE2JR_SAMPLING_SUMMARY="${PHASE2JR_AUDIT_ROOT}/aggregate_safe/input_content_audit/locked_roster/audit_sampling_summary.json"
PHASE2JR_AVAILABILITY="${PHASE2JR_AUDIT_ROOT}/aggregate_safe/input_content_audit/source_availability/source_availability_summary.json"
PHASE2JR_CLIP_ROSTER="${PHASE2JR_AUDIT_ROOT}/restricted/input_content_audit/locked_roster/canonical_clip_roster_restricted.csv"
PHASE2JR_AUDIT_LINKAGE="${PHASE2JR_AUDIT_ROOT}/restricted/input_content_audit/locked_roster/audit_linkage.csv"
PHASE2JR_SAMPLING_DESIGN="${PHASE2JR_AUDIT_ROOT}/restricted/input_content_audit/locked_roster/sampling_design_restricted.csv"
PHASE2JR_RESTORATION_MANIFEST="${PHASE2JR_AUDIT_ROOT}/restricted/input_content_audit/source_availability/source_restoration_manifest.csv"
PHASE2JR_PILOT_ROWS="${PHASE2JR_AUDIT_ROOT}/restricted/input_content_audit/technical_pilot/reconstruction_pilot_rows.csv"
PHASE2JR_PRIMARY="${PHASE2JR_AUDIT_ROOT}/restricted/input_content_audit/locked_roster/reader_manifest.csv"
PHASE2JR_SECOND="${PHASE2JR_AUDIT_ROOT}/restricted/input_content_audit/locked_roster/second_reader_manifest.csv"
PHASE2JR_LOCKED_URL_LIST="/restricted/project/mimicecho/outputs/jdim_phase2i_restoration_v1/restricted/restoration/locked_official_source_urls.txt"
PHASE2JR_LOCKED_URL_SHA256="faec57d91dd054fbcc553d8ae92993b915ed1b25fed02fcc59bdd1bf1685ba21"

phase2jr_require_file() {
  if [[ ! -f "$1" ]]; then
    echo "[error] Required locked artifact is missing" >&2
    return 2
  fi
}

phase2jr_verify_sha256() {
  local observed
  observed="$(sha256sum "$1" | awk '{print $1}')"
  if [[ "${observed}" != "$2" ]]; then
    echo "[error] Locked artifact hash mismatch" >&2
    return 2
  fi
}

phase2jr_verify_upstream_accounting() {
  local job_id="$1"
  local certificate_status="$2"
  local accounting=""
  if [[ ! "${job_id}" =~ ^[0-9]+$ ]]; then
    echo "[error] upstream scheduler job ID is missing or malformed" >&2
    return 2
  fi
  for _attempt in {1..12}; do
    if accounting="$(qacct -j "${job_id}" 2>/dev/null)"; then
      break
    fi
    sleep 10
  done
  if [[ -z "${accounting}" ]]; then
    echo "[error] upstream scheduler accounting is unavailable" >&2
    return 2
  fi
  printf '%s\n' "${accounting}" | "${PHASE2JR_PY}" \
    "${PHASE2JR_REPO}/scripts/run_jdim_phase2jr.py" verify-qacct-certificate \
    --expected-jobnumber "${job_id}" \
    --certificate-status "${certificate_status}"
}

phase2jr_common_preflight() {
  local require_auth="${1:-no}"
  if [[ -z "${PHASE2JR_SOURCE_COMMIT}" ]]; then
    echo "[error] JDIM_PHASE2JR_SOURCE_COMMIT is required" >&2
    return 2
  fi
  for path in \
    "${PHASE2JR_PY}" \
    "${PHASE2JR_CANONICAL_CERT}" \
    "${PHASE2JR_COHORT_ROOT}/cohort_flow_lock.json" \
    "${PHASE2JR_COHORT_ROOT}/cohort_flow_summary.json" \
    "${PHASE2JR_COHORT_ROOT}/cohort_flow_invariants.json" \
    "${PHASE2JR_LOCK}" \
    "${PHASE2JR_SAMPLING_SUMMARY}" \
    "${PHASE2JR_AVAILABILITY}" \
    "${PHASE2JR_CLIP_ROSTER}" \
    "${PHASE2JR_AUDIT_LINKAGE}" \
    "${PHASE2JR_SAMPLING_DESIGN}" \
    "${PHASE2JR_RESTORATION_MANIFEST}" \
    "${PHASE2JR_PILOT_ROWS}" \
    "${PHASE2JR_PRIMARY}" \
    "${PHASE2JR_SECOND}" \
    "${PHASE2JR_LOCKED_URL_LIST}" \
    "${PHASE2JR_EVIDENCE_ROOT}/aggregate_safe/preflight/restoration_state_summary.json" \
    "${PHASE2JR_EVIDENCE_ROOT}/restricted/preflight/restoration_state_restricted.csv"; do
    phase2jr_require_file "${path}" || return $?
  done

  phase2jr_verify_sha256 "${PHASE2JR_CANONICAL_CERT}" "bbd38f8f913c74ab97d005e833a470cf55f25cc0c18e0063854ea5b6528426a2" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_COHORT_ROOT}/cohort_flow_lock.json" "fb528a9611cf512b305c431ceffd705b6680ca9a24dadc14080731786933f233" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_COHORT_ROOT}/cohort_flow_summary.json" "aa8bda7991e341cfdc5cfb84407c3005bb01062258211f886d1c98c53d8014c8" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_COHORT_ROOT}/cohort_flow_invariants.json" "13a0fdfe06c92e441c6e0c30c1ed4722e94954e4869399daff4333a261ab45fa" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_LOCK}" "5c9d4ac72b62c6bec6fa09735535c0b2625f8de09572e7006dd91c46df5019ef" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_SAMPLING_SUMMARY}" "272e755825b793e376825d9eddb795876dfdb60c41b2ec641a12758be1a58607" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_AVAILABILITY}" "89f4d787168b118a6e73b03d0698015bfc93ccf2a5f92b97ab1f1172a57a121d" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_CLIP_ROSTER}" "4220c60eacc201c9de4193719fc79b4cd78289909f7169ba618ecbb8af52c0eb" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_AUDIT_LINKAGE}" "94d5142bf44045903f008f614d8ae24999d93d1700054bac9b5c4f0aa094b667" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_SAMPLING_DESIGN}" "99a30d5c4de4462d60636dc1f16a949ccd7393f2634dddb8b02287fe4a464dc0" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_RESTORATION_MANIFEST}" "1340fbe77e36f9c47b29f551fa399037f24d88e2af631da7eab009812eedfe3c" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_PILOT_ROWS}" "acf7040135d3ce892dd0164750f65ede1c035585e7f7c1869fead258662d6356" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_PRIMARY}" "d042fbc621fed3a97c2f68581e7983e46d3b559c32dfa47c45cedb886ea3b0a6" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_SECOND}" "d130fa54e1142323fd515ccc72d162ae7f6c4c31fe8887b67a3cd339dbb1ec64" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_LOCKED_URL_LIST}" "${PHASE2JR_LOCKED_URL_SHA256}" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_EVIDENCE_ROOT}/aggregate_safe/preflight/restoration_state_summary.json" "13415d487236678023add790edda56d3d559080932a646f0c1266013cd45fc1f" || return $?
  phase2jr_verify_sha256 "${PHASE2JR_EVIDENCE_ROOT}/restricted/preflight/restoration_state_restricted.csv" "0b722cbf7bc2656cdc1418ec5ad10ea1d9a053ef1c1c5cca941ffdc627323ea2" || return $?
  if [[ "$("${PHASE2JR_PY}" -c "import json; print(json.load(open('${PHASE2JR_EVIDENCE_ROOT}/aggregate_safe/preflight/restoration_state_summary.json'))['remaining_file_set_sha256'])")" != "1da97b5abdfe5272fd59888ce60f6be0e0150b9230f875a613bf40ca4d863ddd" ]]; then
    echo "[error] preserved Phase 2J-R remaining-file hash mismatch" >&2
    return 2
  fi
  if [[ "$(wc -l < "${PHASE2JR_LOCKED_URL_LIST}" | tr -d ' ')" != "4808" ]]; then
    echo "[error] locked official-source URL count changed" >&2
    return 2
  fi

  if [[ "$(git -C "${PHASE2JR_REPO}" rev-parse HEAD)" != "${PHASE2JR_SOURCE_COMMIT}" ]]; then
    echo "[error] SCC checkout is not at JDIM_PHASE2JR_SOURCE_COMMIT" >&2
    return 2
  fi
  if ! git -C "${PHASE2JR_REPO}" merge-base --is-ancestor "${PHASE2JR_AUTHORIZED_PARENT}" "${PHASE2JR_SOURCE_COMMIT}" \
    || [[ "$(git -C "${PHASE2JR_REPO}" rev-list --count "${PHASE2JR_AUTHORIZED_PARENT}..${PHASE2JR_SOURCE_COMMIT}")" != "1" ]]; then
    echo "[error] source is not the single authorized Phase 2J-R2 operational repair" >&2
    return 2
  fi
  if [[ -n "$(git -C "${PHASE2JR_REPO}" status --porcelain)" ]]; then
    echo "[error] SCC checkout is not clean" >&2
    return 2
  fi
  "${PHASE2JR_PY}" -m py_compile \
    "${PHASE2JR_REPO}/scripts/jdim_tier1/phase2jr.py" \
    "${PHASE2JR_REPO}/scripts/run_jdim_phase2jr.py"

  if [[ "${require_auth}" == "yes" ]]; then
    if [[ ! -f "${HOME}/.netrc" || "$(stat -c '%a' "${HOME}/.netrc")" != "600" ]]; then
      echo "[error] BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION" >&2
      return 2
    fi
    if ! wget --quiet --spider --netrc --https-only --max-redirect=0 \
      "https://physionet.org/files/mimic-iv-echo/1.0/"; then
      echo "[error] BLOCKED_OFFICIAL_SOURCE_AUTHENTICATION" >&2
      return 2
    fi
  fi
}
