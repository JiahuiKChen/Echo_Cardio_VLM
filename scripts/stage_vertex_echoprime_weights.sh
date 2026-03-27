#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Stage EchoPrime checkpoint weights to GCS for Vertex workers.

Usage:
  ./scripts/stage_vertex_echoprime_weights.sh \
    [--weights-dir /Users/.../EchoPrime/model_data/weights] \
    [--gcs-prefix gs://mimicuscore/echo_ai/shared/echoprime_weights]
EOF
}

WEIGHTS_DIR="/Users/paulkarim/mimic-code/ECHO AI/EchoPrime/model_data/weights"
GCS_PREFIX="gs://mimicuscore/echo_ai/shared/echoprime_weights"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --weights-dir) WEIGHTS_DIR="$2"; shift 2 ;;
    --gcs-prefix) GCS_PREFIX="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! -d "${WEIGHTS_DIR}" ]]; then
  echo "[error] Weights dir not found: ${WEIGHTS_DIR}" >&2
  exit 1
fi
if [[ ! -f "${WEIGHTS_DIR}/echo_prime_encoder.pt" ]]; then
  echo "[error] Missing ${WEIGHTS_DIR}/echo_prime_encoder.pt" >&2
  exit 1
fi
if [[ ! -f "${WEIGHTS_DIR}/view_classifier.pt" ]]; then
  echo "[error] Missing ${WEIGHTS_DIR}/view_classifier.pt" >&2
  exit 1
fi

echo "[info] Uploading weights from ${WEIGHTS_DIR} to ${GCS_PREFIX}"
gcloud storage cp "${WEIGHTS_DIR}/echo_prime_encoder.pt" "${GCS_PREFIX}/echo_prime_encoder.pt"
gcloud storage cp "${WEIGHTS_DIR}/view_classifier.pt" "${GCS_PREFIX}/view_classifier.pt"

echo "[done] Weights staging complete."
echo "[done] Weights prefix: ${GCS_PREFIX}"
