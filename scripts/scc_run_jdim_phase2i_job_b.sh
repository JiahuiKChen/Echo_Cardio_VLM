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
JOB_A_ROOT="${JDIM_PHASE2I_JOB_A_ROOT:-/restricted/project/mimicecho/outputs/jdim_phase2i_restoration_v1}"
OUT="/restricted/project/mimicecho/outputs/jdim_phase2i_audit_interface_v1"
AUDIT_ROOT="/restricted/project/mimicecho/outputs/jdim_audit_roster_pilot_v1"
TECHNICAL_SUMMARY="${JOB_A_ROOT}/aggregate_safe/technical_inventory/technical_input_inventory_summary.json"
TECHNICAL_INVENTORY="${JOB_A_ROOT}/restricted/technical_inventory/technical_input_inventory_restricted.csv"
PRIMARY="${AUDIT_ROOT}/restricted/input_content_audit/locked_roster/reader_manifest.csv"
SECOND="${AUDIT_ROOT}/restricted/input_content_audit/locked_roster/second_reader_manifest.csv"

if [[ -z "${SOURCE_COMMIT}" ]]; then
  echo "[error] JDIM_PHASE2I_SOURCE_COMMIT is required" >&2
  exit 2
fi
if [[ ! "${JOB_A_ROOT}" =~ ^/restricted/project/mimicecho/outputs/jdim_phase2i_restoration_v[0-9]+$ ]]; then
  echo "[error] JDIM_PHASE2I_JOB_A_ROOT is outside the authorized versioned root family" >&2
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

"${PY}" -c "import json; d=json.load(open('${OUT}/restricted/interface/interface_summary.json')); assert d['primary_studies']==116 and d['second_reader_studies']==24 and d['clips']==5071 and d['reader_blinding_validated'] is True and d['ocr_available'] is False and d['automated_content_annotation'] is False"

"${PY}" -c "import json,pathlib; root=pathlib.Path('${OUT}'); interface=json.load(open(root/'restricted/interface/interface_summary.json')); media=json.load(open(root/'restricted/audit_media/audit_media_summary.json')); payload={'status':'READY_FOR_BLINDED_HUMAN_AUDIT','interface':interface,'media':media}; out=root/'aggregate_safe/phase2i_job_b_summary.json'; out.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')"
echo "READY_FOR_BLINDED_HUMAN_AUDIT"
