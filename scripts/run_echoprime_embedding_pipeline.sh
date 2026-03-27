#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run EchoPrime embedding extraction + embedding baseline on a processed cohort.

Usage:
  ./scripts/run_echoprime_embedding_pipeline.sh \
    [--cohort-root /Users/.../outputs/cloud_cohorts/stage_d_500study] \
    [--weights-dir /Users/.../EchoPrime/model_data/weights] \
    [--python-bin python3] \
    [--device cpu|mps|cuda|auto] \
    [--batch-size 8] \
    [--max-clips 0] \
    [--npz-path-prefix-from /old/prefix] \
    [--npz-path-prefix-to /new/prefix]

Inputs expected under cohort-root:
  extract_smoke/extraction_manifest.csv
  manifests/lvef_still_manifest.csv
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COHORT_ROOT="${REPO_ROOT}/outputs/cloud_cohorts/stage_d_500study"
WEIGHTS_DIR="${REPO_ROOT}/EchoPrime/model_data/weights"
PYTHON_BIN="python3"
DEVICE="cpu"
BATCH_SIZE=8
MAX_CLIPS=0
NPZ_PATH_PREFIX_FROM=""
NPZ_PATH_PREFIX_TO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cohort-root) COHORT_ROOT="$2"; shift 2 ;;
    --weights-dir) WEIGHTS_DIR="$2"; shift 2 ;;
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --max-clips) MAX_CLIPS="$2"; shift 2 ;;
    --npz-path-prefix-from) NPZ_PATH_PREFIX_FROM="$2"; shift 2 ;;
    --npz-path-prefix-to) NPZ_PATH_PREFIX_TO="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[error] Python executable not found/executable: ${PYTHON_BIN}" >&2
  exit 1
fi
if [[ ! -d "${COHORT_ROOT}" ]]; then
  echo "[error] Cohort root not found: ${COHORT_ROOT}" >&2
  exit 1
fi
if [[ ! -d "${WEIGHTS_DIR}" ]]; then
  echo "[error] Weights dir not found: ${WEIGHTS_DIR}" >&2
  exit 1
fi
if [[ -n "${NPZ_PATH_PREFIX_FROM}" && -z "${NPZ_PATH_PREFIX_TO}" ]]; then
  echo "[error] --npz-path-prefix-to is required when --npz-path-prefix-from is set." >&2
  exit 1
fi
if [[ -z "${NPZ_PATH_PREFIX_FROM}" && -n "${NPZ_PATH_PREFIX_TO}" ]]; then
  echo "[error] --npz-path-prefix-from is required when --npz-path-prefix-to is set." >&2
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

EMB_DIR="${COHORT_ROOT}/echoprime_embeddings"
EMB_NPZ="${EMB_DIR}/clip_embeddings_523.npz"
EMB_MANIFEST="${EMB_DIR}/clip_embedding_manifest.csv"
BASELINE_DIR="${COHORT_ROOT}/baseline_lvef_echoprime_embeddings"

mkdir -p "${EMB_DIR}" "${BASELINE_DIR}"

echo "[info] Extracting EchoPrime embeddings..."
extract_cmd=(
  "${PYTHON_BIN}" "${SCRIPT_DIR}/extract_echoprime_embeddings.py"
  --extraction-manifest "${EXTRACT_MANIFEST}"
  --weights-dir "${WEIGHTS_DIR}"
  --output-npz "${EMB_NPZ}"
  --output-manifest "${EMB_MANIFEST}"
  --device "${DEVICE}"
  --batch-size "${BATCH_SIZE}"
  --max-clips "${MAX_CLIPS}"
)
if [[ -n "${NPZ_PATH_PREFIX_FROM}" ]]; then
  extract_cmd+=(--path-prefix-from "${NPZ_PATH_PREFIX_FROM}" --path-prefix-to "${NPZ_PATH_PREFIX_TO}")
fi
"${extract_cmd[@]}"

echo "[info] Running embedding baseline..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/run_echoprime_embedding_baseline.py" \
  --embedding-npz "${EMB_NPZ}" \
  --embedding-manifest "${EMB_MANIFEST}" \
  --label-manifest "${LVEF_MANIFEST}" \
  --output-dir "${BASELINE_DIR}"

echo "[done] EchoPrime embedding pipeline completed."
echo "[done] Embedding NPZ: ${EMB_NPZ}"
echo "[done] Embedding manifest: ${EMB_MANIFEST}"
echo "[done] Baseline metrics: ${BASELINE_DIR}/echoprime_embedding_baseline_metrics.json"
