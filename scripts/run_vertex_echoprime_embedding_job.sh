#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run EchoPrime embedding pipeline on Vertex AI worker.

Usage:
  /workspace/scripts/run_vertex_echoprime_embedding_job.sh \
    --gcs-input-prefix gs://bucket/path/to/stage_d_inputs \
    --gcs-weights-prefix gs://bucket/path/to/echoprime_weights \
    --gcs-output-prefix gs://bucket/path/to/stage_d_outputs \
    [--workspace-root /workspace/cloud_work] \
    [--device cuda] \
    [--batch-size 8] \
    [--max-clips 0]
EOF
}

GCS_INPUT_PREFIX=""
GCS_WEIGHTS_PREFIX=""
GCS_OUTPUT_PREFIX=""
WORKSPACE_ROOT="/workspace/cloud_work"
DEVICE="cuda"
BATCH_SIZE=8
MAX_CLIPS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gcs-input-prefix) GCS_INPUT_PREFIX="$2"; shift 2 ;;
    --gcs-weights-prefix) GCS_WEIGHTS_PREFIX="$2"; shift 2 ;;
    --gcs-output-prefix) GCS_OUTPUT_PREFIX="$2"; shift 2 ;;
    --workspace-root) WORKSPACE_ROOT="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --max-clips) MAX_CLIPS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${GCS_INPUT_PREFIX}" || -z "${GCS_WEIGHTS_PREFIX}" || -z "${GCS_OUTPUT_PREFIX}" ]]; then
  echo "[error] --gcs-input-prefix, --gcs-weights-prefix, and --gcs-output-prefix are required." >&2
  usage
  exit 1
fi

REPO_ROOT="/workspace"
COHORT_ROOT="${WORKSPACE_ROOT}/cohort"
WEIGHTS_ROOT="${WORKSPACE_ROOT}/weights"
LOG_ROOT="${WORKSPACE_ROOT}/logs"
mkdir -p "${COHORT_ROOT}" "${WEIGHTS_ROOT}" "${LOG_ROOT}"

echo "[info] Downloading staged cohort inputs from ${GCS_INPUT_PREFIX}"
python3 "${REPO_ROOT}/scripts/gcs_sync.py" download-prefix \
  --gs-uri "${GCS_INPUT_PREFIX}" \
  --local-dir "${COHORT_ROOT}"

echo "[info] Downloading EchoPrime weights from ${GCS_WEIGHTS_PREFIX}"
python3 "${REPO_ROOT}/scripts/gcs_sync.py" download-prefix \
  --gs-uri "${GCS_WEIGHTS_PREFIX}" \
  --local-dir "${WEIGHTS_ROOT}"

EXTRACT_MANIFEST="${COHORT_ROOT}/extract_smoke/extraction_manifest.csv"
LVEF_MANIFEST="${COHORT_ROOT}/manifests/lvef_still_manifest.csv"
NPZ_LOCAL_ROOT="${COHORT_ROOT}/derived/smoke_npz"

if [[ ! -f "${EXTRACT_MANIFEST}" ]]; then
  echo "[error] Missing extraction manifest after download: ${EXTRACT_MANIFEST}" >&2
  exit 1
fi
if [[ ! -f "${LVEF_MANIFEST}" ]]; then
  echo "[error] Missing LVEF manifest after download: ${LVEF_MANIFEST}" >&2
  exit 1
fi
if [[ ! -d "${NPZ_LOCAL_ROOT}" ]]; then
  echo "[error] Missing NPZ root after download: ${NPZ_LOCAL_ROOT}" >&2
  exit 1
fi
if [[ ! -f "${WEIGHTS_ROOT}/echo_prime_encoder.pt" ]]; then
  echo "[error] Missing weights file after download: ${WEIGHTS_ROOT}/echo_prime_encoder.pt" >&2
  exit 1
fi
if [[ ! -f "${WEIGHTS_ROOT}/view_classifier.pt" ]]; then
  echo "[error] Missing weights file after download: ${WEIGHTS_ROOT}/view_classifier.pt" >&2
  exit 1
fi

NPZ_PREFIX_FROM="$(
python3 - <<'PY' "${EXTRACT_MANIFEST}"
import csv
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
marker = "/derived/smoke_npz"
with manifest.open(newline="") as f:
    reader = csv.DictReader(f)
    source = None
    for row in reader:
        output_path = str(row.get("output_path", "")).strip()
        if output_path:
            source = output_path
            break

if not source:
    raise SystemExit("[error] extraction_manifest.csv has no non-empty output_path values.")

idx = source.find(marker)
if idx < 0:
    raise SystemExit(f"[error] Could not infer NPZ prefix marker '{marker}' from output_path: {source}")

print(source[: idx + len(marker)])
PY
)"

echo "[info] Path rewrite for NPZ entries:"
echo "       from: ${NPZ_PREFIX_FROM}"
echo "       to:   ${NPZ_LOCAL_ROOT}"

START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SECONDS=0

"${REPO_ROOT}/scripts/run_echoprime_embedding_pipeline.sh" \
  --cohort-root "${COHORT_ROOT}" \
  --weights-dir "${WEIGHTS_ROOT}" \
  --python-bin "python3" \
  --device "${DEVICE}" \
  --batch-size "${BATCH_SIZE}" \
  --max-clips "${MAX_CLIPS}" \
  --npz-path-prefix-from "${NPZ_PREFIX_FROM}" \
  --npz-path-prefix-to "${NPZ_LOCAL_ROOT}"

ELAPSED="${SECONDS}"
END_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

RUN_SUMMARY_JSON="${LOG_ROOT}/vertex_embedding_run_summary.json"
cat > "${RUN_SUMMARY_JSON}" <<EOF
{
  "start_utc": "${START_TS}",
  "end_utc": "${END_TS}",
  "elapsed_seconds": ${ELAPSED},
  "gcs_input_prefix": "${GCS_INPUT_PREFIX}",
  "gcs_weights_prefix": "${GCS_WEIGHTS_PREFIX}",
  "gcs_output_prefix": "${GCS_OUTPUT_PREFIX}",
  "cohort_root": "${COHORT_ROOT}",
  "device": "${DEVICE}",
  "batch_size": ${BATCH_SIZE},
  "max_clips": ${MAX_CLIPS},
  "npz_path_prefix_from": "${NPZ_PREFIX_FROM}",
  "npz_path_prefix_to": "${NPZ_LOCAL_ROOT}",
  "metrics_json": "${COHORT_ROOT}/baseline_lvef_echoprime_embeddings/echoprime_embedding_baseline_metrics.json"
}
EOF

echo "[info] Uploading outputs to ${GCS_OUTPUT_PREFIX}"
python3 "${REPO_ROOT}/scripts/gcs_sync.py" upload-dir \
  --local-dir "${COHORT_ROOT}/echoprime_embeddings" \
  --gs-uri "${GCS_OUTPUT_PREFIX}/echoprime_embeddings"
python3 "${REPO_ROOT}/scripts/gcs_sync.py" upload-dir \
  --local-dir "${COHORT_ROOT}/baseline_lvef_echoprime_embeddings" \
  --gs-uri "${GCS_OUTPUT_PREFIX}/baseline_lvef_echoprime_embeddings"
python3 "${REPO_ROOT}/scripts/gcs_sync.py" upload-dir \
  --local-dir "${LOG_ROOT}" \
  --gs-uri "${GCS_OUTPUT_PREFIX}/logs"

echo "[done] Vertex embedding job completed."
echo "[done] Output prefix: ${GCS_OUTPUT_PREFIX}"
