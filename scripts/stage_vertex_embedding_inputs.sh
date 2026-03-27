#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Stage processed cohort inputs to GCS for Vertex embedding runs.

Usage:
  ./scripts/stage_vertex_embedding_inputs.sh \
    --cohort-root /Users/.../outputs/cloud_cohorts/stage_d_500study \
    --gcs-prefix gs://mimicuscore/echo_ai/stage_d_500study/vertex_inputs \
    [--npz-root /Volumes/.../derived/smoke_npz]

Uploads:
  extract_smoke/extraction_manifest.csv
  manifests/lvef_still_manifest.csv
  derived/smoke_npz/** (from --npz-root or inferred from extraction manifest)
EOF
}

COHORT_ROOT=""
GCS_PREFIX=""
NPZ_ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cohort-root) COHORT_ROOT="$2"; shift 2 ;;
    --gcs-prefix) GCS_PREFIX="$2"; shift 2 ;;
    --npz-root) NPZ_ROOT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${COHORT_ROOT}" || -z "${GCS_PREFIX}" ]]; then
  echo "[error] --cohort-root and --gcs-prefix are required." >&2
  usage
  exit 1
fi

EXTRACT_MANIFEST="${COHORT_ROOT}/extract_smoke/extraction_manifest.csv"
LVEF_MANIFEST="${COHORT_ROOT}/manifests/lvef_still_manifest.csv"
if [[ ! -f "${EXTRACT_MANIFEST}" ]]; then
  echo "[error] Missing extraction manifest: ${EXTRACT_MANIFEST}" >&2
  exit 1
fi
if [[ ! -f "${LVEF_MANIFEST}" ]]; then
  echo "[error] Missing LVEF manifest: ${LVEF_MANIFEST}" >&2
  exit 1
fi

if [[ -z "${NPZ_ROOT}" ]]; then
  NPZ_ROOT="$(
  python3 - <<'PY' "${EXTRACT_MANIFEST}"
import csv
import sys

manifest = sys.argv[1]
marker = "/derived/smoke_npz"
source = None
with open(manifest, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        out = str(row.get("output_path", "")).strip()
        if out:
            source = out
            break

if not source:
    raise SystemExit("[error] Could not infer NPZ root from extraction manifest.")
idx = source.find(marker)
if idx < 0:
    raise SystemExit(f"[error] Could not infer NPZ root marker '{marker}' from: {source}")
print(source[: idx + len(marker)])
PY
  )"
fi

if [[ ! -d "${NPZ_ROOT}" ]]; then
  echo "[error] NPZ root directory does not exist: ${NPZ_ROOT}" >&2
  exit 1
fi

echo "[info] Uploading manifests to ${GCS_PREFIX}"
gcloud storage cp "${EXTRACT_MANIFEST}" "${GCS_PREFIX}/extract_smoke/extraction_manifest.csv"
gcloud storage cp "${LVEF_MANIFEST}" "${GCS_PREFIX}/manifests/lvef_still_manifest.csv"

echo "[info] Uploading NPZ tree from ${NPZ_ROOT}"
gcloud storage cp --recursive "${NPZ_ROOT}" "${GCS_PREFIX}/derived/"

echo "[done] Staging complete."
echo "[done] Input prefix: ${GCS_PREFIX}"
