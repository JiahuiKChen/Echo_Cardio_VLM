#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Download and stage EchoPrime weights on SCC.

Checks for weights in order:
  1) Local repo path: EchoPrime/model_data/weights/
  2) SCC project storage: /restricted/project/mimicecho/echoprime_weights/
  3) GitHub release download (model_data.zip from echonet/EchoPrime v1.0.0)

Usage:
  ./scripts/scc_stage_echoprime_weights.sh [--output-dir <path>]
EOF
}

OUTPUT_DIR="${ECHO_AI_RESTRICTED_PROJECT:-/restricted/project/mimicecho}/echoprime_weights"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

ENCODER_FILE="echo_prime_encoder.pt"
VIEW_FILE="view_classifier.pt"

check_weights() {
  local dir="$1"
  [[ -f "${dir}/${ENCODER_FILE}" && -f "${dir}/${VIEW_FILE}" ]]
}

if check_weights "${REPO_ROOT}/EchoPrime/model_data/weights"; then
  echo "[info] Weights found in repo: ${REPO_ROOT}/EchoPrime/model_data/weights"
  if [[ "${REPO_ROOT}/EchoPrime/model_data/weights" != "${OUTPUT_DIR}" ]]; then
    mkdir -p "${OUTPUT_DIR}"
    cp "${REPO_ROOT}/EchoPrime/model_data/weights/${ENCODER_FILE}" "${OUTPUT_DIR}/"
    cp "${REPO_ROOT}/EchoPrime/model_data/weights/${VIEW_FILE}" "${OUTPUT_DIR}/"
    echo "[info] Copied weights to ${OUTPUT_DIR}"
  fi
elif check_weights "${OUTPUT_DIR}"; then
  echo "[info] Weights already staged at ${OUTPUT_DIR}"
else
  echo "[info] Weights not found locally. Downloading from GitHub release..."
  mkdir -p "${OUTPUT_DIR}"
  TMPDIR="$(mktemp -d)"
  trap 'rm -rf "${TMPDIR}"' EXIT

  RELEASE_URL="https://github.com/echonet/EchoPrime/releases/download/v1.0.0/model_data.zip"
  echo "[info] Downloading ${RELEASE_URL} ..."

  if command -v wget >/dev/null 2>&1; then
    wget -q --show-progress -O "${TMPDIR}/model_data.zip" "${RELEASE_URL}"
  elif command -v curl >/dev/null 2>&1; then
    curl -L -o "${TMPDIR}/model_data.zip" "${RELEASE_URL}"
  else
    echo "[error] Neither wget nor curl available." >&2
    exit 1
  fi

  echo "[info] Extracting weights..."
  if command -v unzip >/dev/null 2>&1; then
    unzip -q -o "${TMPDIR}/model_data.zip" -d "${TMPDIR}/extracted"
  else
    python3 -c "
import zipfile, sys
with zipfile.ZipFile('${TMPDIR}/model_data.zip', 'r') as z:
    z.extractall('${TMPDIR}/extracted')
"
  fi

  FOUND_ENCODER="$(find "${TMPDIR}/extracted" -name "${ENCODER_FILE}" -type f | head -1)"
  FOUND_VIEW="$(find "${TMPDIR}/extracted" -name "${VIEW_FILE}" -type f | head -1)"

  if [[ -z "${FOUND_ENCODER}" || -z "${FOUND_VIEW}" ]]; then
    echo "[error] Could not find ${ENCODER_FILE} and ${VIEW_FILE} in extracted archive." >&2
    echo "[info] Archive contents:" >&2
    find "${TMPDIR}/extracted" -type f | head -20 >&2
    exit 1
  fi

  cp "${FOUND_ENCODER}" "${OUTPUT_DIR}/${ENCODER_FILE}"
  cp "${FOUND_VIEW}" "${OUTPUT_DIR}/${VIEW_FILE}"
  echo "[info] Weights staged to ${OUTPUT_DIR}"
fi

echo "[done] Weights ready:"
ls -lh "${OUTPUT_DIR}/${ENCODER_FILE}" "${OUTPUT_DIR}/${VIEW_FILE}"
echo "[done] Use --weights-dir ${OUTPUT_DIR}"
