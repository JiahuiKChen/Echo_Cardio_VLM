#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Check gcloud/bq/gsutil auth status on SCC.

Usage:
  ./scripts/scc_check_gcloud_auth.sh [--billing-project mimic-iv-anesthesia]
EOF
}

BILLING_PROJECT="${ECHO_AI_BILLING_PROJECT:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --billing-project) BILLING_PROJECT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

module load python3/3.10.12
module load google-cloud-sdk/455.0.0

echo "[info] gcloud accounts:"
gcloud auth list || true

echo "[info] active gcloud account:"
gcloud config get-value account || true

if [[ -n "${BILLING_PROJECT}" ]]; then
  echo "[info] bq probe:"
  bq --project_id="${BILLING_PROJECT}" ls "physionet-data:mimiciv_echo" || true
  echo "[info] gcs probe:"
  gcloud storage ls --billing-project="${BILLING_PROJECT}" "gs://mimic-iv-echo-1.0.physionet.org" | head -n 5 || true
fi
