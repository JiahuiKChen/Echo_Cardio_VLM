#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Diagnose SCC project storage provisioning for this project and emit a support-ready report.

Usage:
  ./scripts/scc_storage_blocker_report.sh [--project-name mimicecho]
EOF
}

PROJECT_NAME="mimicecho"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-name) PROJECT_NAME="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

OUT_DIR="outputs/scc_storage_blocker"
mkdir -p "${OUT_DIR}"
STAMP="$(date +%Y%m%d_%H%M%S)"
REPORT="${OUT_DIR}/scc_storage_blocker_${STAMP}.txt"

{
  echo "SCC Storage Blocker Report"
  echo "Generated: $(date)"
  echo
  echo "[identity]"
  hostname || true
  id || true
  groups || true
  echo
  echo "[project-path-check]"
  for p in \
    "/project/${PROJECT_NAME}" \
    "/projectnb/${PROJECT_NAME}" \
    "/restricted/project/${PROJECT_NAME}" \
    "/restricted/projectnb/${PROJECT_NAME}"
  do
    echo ">> ls -ld ${p}"
    ls -ld "${p}" 2>&1 || true
  done
  echo
  echo "[top-level-mount-check]"
  ls -ld /project /projectnb /restricted /restricted/project /restricted/projectnb 2>&1 || true
  echo
  echo "[group-entries]"
  getent group "${PROJECT_NAME}" 2>&1 || true
  getent group mimicgrp 2>&1 || true
  echo
  echo "[nearby-project-names]"
  ls -1 /project 2>/dev/null | grep -i mimic || true
  ls -1 /projectnb 2>/dev/null | grep -i mimic || true
  echo
  echo "[writability-test]"
  for p in "/project/${PROJECT_NAME}" "/projectnb/${PROJECT_NAME}"; do
    echo ">> touch ${p}/.__echo_ai_write_test"
    touch "${p}/.__echo_ai_write_test" 2>&1 || true
    rm -f "${p}/.__echo_ai_write_test" 2>/dev/null || true
  done
  echo
  echo "[quota]"
  quota -s || true
  echo
  echo "[support-ticket-template]"
  cat <<TEMPLATE
Hello SCC Support,

I need restricted project storage provisioned for project group '${PROJECT_NAME}' so we can run a confidential MIMIC-IV-ECHO pipeline on SCC.

Current blocker from login nodes:
- group '${PROJECT_NAME}' exists for user '${USER}'
- /project/${PROJECT_NAME} and /projectnb/${PROJECT_NAME} do not exist (or are not writable)
- /restricted/project/${PROJECT_NAME} and /restricted/projectnb/${PROJECT_NAME} are not present from SSH shells

Please provision and confirm writable project paths for this group on login and batch nodes:
- /project/${PROJECT_NAME}
- /projectnb/${PROJECT_NAME}
and if applicable their /restricted aliases.

This project handles DUA/HIPAA-limited data and must run/store in the SCC restricted project space, not home or local scratch fallback.

Thanks.
TEMPLATE
} | tee "${REPORT}"

echo "[written] ${REPORT}"
