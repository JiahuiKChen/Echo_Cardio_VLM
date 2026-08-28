#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
if [[ "${MODE}" != "run" && "${MODE}" != "preflight" ]]; then
  echo "[error] Usage: scripts/scc_run_jdim_phase2i_job_b.sh [preflight|run]" >&2
  exit 2
fi

REPO="${JDIM_REPO_ROOT:-/restricted/project/mimicecho/code/Echo_Cardio_VLM_jdim_phase2er_driver_fix}"
PY="${JDIM_PYTHON_BIN:-/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python}"
SOURCE_COMMIT="${JDIM_PHASE2I_SOURCE_COMMIT:-}"
AUTHORIZED_BASE_COMMIT="58e9de15fba97a7f2126703a1eb6b52d95ff9c63"
JOB_A_ROOT="${JDIM_PHASE2I_JOB_A_ROOT:-/restricted/project/mimicecho/outputs/jdim_phase2j_restoration_v2}"
OUT="${JDIM_PHASE2I_JOB_B_ROOT:-/restricted/project/mimicecho/outputs/jdim_phase2j_audit_interface_v1}"
EXPECTED_JOB_A_ROOT="/restricted/project/mimicecho/outputs/jdim_phase2j_restoration_v2"
EXPECTED_OUT="/restricted/project/mimicecho/outputs/jdim_phase2j_audit_interface_v1"
AUDIT_ROOT="/restricted/project/mimicecho/outputs/jdim_audit_roster_pilot_v1"
TECHNICAL_SUMMARY="${JOB_A_ROOT}/aggregate_safe/technical_inventory/technical_input_inventory_summary.json"
TECHNICAL_INVENTORY="${JOB_A_ROOT}/restricted/technical_inventory/technical_input_inventory_restricted.csv"
PRIMARY="${AUDIT_ROOT}/restricted/input_content_audit/locked_roster/reader_manifest.csv"
SECOND="${AUDIT_ROOT}/restricted/input_content_audit/locked_roster/second_reader_manifest.csv"

if [[ -z "${SOURCE_COMMIT}" ]]; then
  echo "[error] JDIM_PHASE2I_SOURCE_COMMIT is required" >&2
  exit 2
fi
if [[ "${JOB_A_ROOT}" != "${EXPECTED_JOB_A_ROOT}" || "${OUT}" != "${EXPECTED_OUT}" ]]; then
  echo "[error] Phase 2J Job A or Job B root differs from the authorized immutable root" >&2
  exit 2
fi
for path in "${PY}" "${TECHNICAL_SUMMARY}" "${TECHNICAL_INVENTORY}" "${PRIMARY}" "${SECOND}"; do
  if [[ ! -f "${path}" ]]; then
    echo "[error] Required file is missing: ${path}" >&2
    exit 2
  fi
done
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
if [[ "$("${PY}" -c "import json; print(json.load(open('${TECHNICAL_SUMMARY}'))['status'])")" != "AUDIT_INPUTS_TECHNICALLY_LOCKED" ]]; then
  echo "[error] Job A did not pass AUDIT_INPUTS_TECHNICALLY_LOCKED" >&2
  exit 2
fi
if [[ "$(sha256sum "${TECHNICAL_INVENTORY}" | awk '{print $1}')" != "$("${PY}" -c "import json; print(json.load(open('${TECHNICAL_SUMMARY}'))['technical_inventory_sha256'])")" ]]; then
  echo "[error] technical inventory no longer matches its Job A summary" >&2
  exit 2
fi
if [[ "$(sha256sum "${PRIMARY}" | awk '{print $1}')" != "d042fbc621fed3a97c2f68581e7983e46d3b559c32dfa47c45cedb886ea3b0a6" ]]; then
  echo "[error] primary reader manifest hash changed" >&2
  exit 2
fi
if [[ "$(sha256sum "${SECOND}" | awk '{print $1}')" != "d130fa54e1142323fd515ccc72d162ae7f6c4c31fe8887b67a3cd339dbb1ec64" ]]; then
  echo "[error] second-reader manifest hash changed" >&2
  exit 2
fi
if [[ -e "${OUT}" ]]; then
  echo "[error] refusing to overwrite immutable Phase 2I Job B output root" >&2
  exit 2
fi
if [[ "${MODE}" == "preflight" ]]; then
  echo "PHASE2I_JOB_B_PREFLIGHT_OK"
  exit 0
fi

mkdir -p "${OUT}/restricted" "${OUT}/aggregate_safe"
cd "${REPO}"
"${PY}" scripts/run_jdim_phase2i.py build-media \
  --technical-inventory-csv "${TECHNICAL_INVENTORY}" \
  --restricted-output-root "${OUT}/restricted/audit_media"
"${PY}" scripts/run_jdim_phase2i.py build-interface \
  --technical-manifest-csv "${OUT}/restricted/audit_media/technical_interface_manifest_restricted.csv" \
  --reader-manifest-csv "${PRIMARY}" \
  --second-reader-manifest-csv "${SECOND}" \
  --media-root "${OUT}/restricted/audit_media/media" \
  --restricted-output-root "${OUT}/restricted/interface"
"${PY}" scripts/run_jdim_phase2i.py validate-interface \
  --interface-root "${OUT}/restricted/interface" \
  --checkpoint-parent "${OUT}/restricted/interface_validation" \
  --safe-output-json "${OUT}/aggregate_safe/interface_validation.json"

"${PY}" -c "import json; d=json.load(open('${OUT}/restricted/interface/interface_summary.json')); assert d['primary_studies']==116 and d['second_reader_studies']==24 and d['clips']==5071 and d['primary_clip_reads']==5071 and d['second_reader_clip_reads']==1043 and d['reader_blinding_validated'] is True and d['ocr_available'] is False and d['automated_content_annotation'] is False and d['completion_validation_before_lock'] is True and d['canonical_media_reused_for_second_reader'] is True and d['public_network_binding_required'] is False"
"${PY}" -c "import json; d=json.load(open('${OUT}/aggregate_safe/interface_validation.json')); assert d['status']=='AUDIT_INTERFACE_VALIDATED' and d['primary_studies']==116 and d['primary_clip_reads']==5071 and d['second_studies']==24 and d['second_clip_reads']==1043 and d['synthetic_annotations_removed'] is True"

"${PY}" -c "import hashlib,json,pathlib; root=pathlib.Path('${OUT}'); interface=json.load(open(root/'restricted/interface/interface_summary.json')); media=json.load(open(root/'restricted/audit_media/audit_media_summary.json')); validation=json.load(open(root/'aggregate_safe/interface_validation.json')); digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(); payload={'status':'READY_FOR_BLINDED_HUMAN_AUDIT','source_commit':'${SOURCE_COMMIT}','interface':interface,'media':media,'validation':validation,'primary_package_sha256':digest(root/'restricted/interface/primary_reader_manifest.json'),'second_reader_package_sha256':digest(root/'restricted/interface/second_reader_manifest.json'),'interface_policy_sha256':digest(root/'restricted/interface/interface_policy.json'),'validation_sha256':digest(root/'aggregate_safe/interface_validation.json')}; out=root/'aggregate_safe/phase2j_job_b_summary.json'; out.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')"
echo "READY_FOR_BLINDED_HUMAN_AUDIT"
