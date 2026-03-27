#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Build/push GPU image and submit Vertex custom job for EchoPrime embedding pipeline.

Usage:
  ./scripts/submit_vertex_echoprime_embedding_job.sh \
    --project mimic-iv-anesthesia \
    --region us-central1 \
    --gcs-input-prefix gs://mimicuscore/echo_ai/stage_d_500study/vertex_inputs \
    --gcs-weights-prefix gs://mimicuscore/echo_ai/shared/echoprime_weights \
    --gcs-output-prefix gs://mimicuscore/echo_ai/stage_d_500study/vertex_runs/run_YYYYMMDD_HHMMSS \
    [--artifact-repo echo-ai] \
    [--image-name echoprime-gpu] \
    [--image-uri us-central1-docker.pkg.dev/.../echo-ai/echoprime-gpu:tag] \
    [--machine-type g2-standard-4] \
    [--accelerator-type NVIDIA_L4] \
    [--accelerator-count 1] \
    [--batch-size 8] \
    [--max-clips 0] \
    [--device cuda] \
    [--build-image true|false] \
    [--service-account SERVICE_ACCOUNT_EMAIL]
EOF
}

to_bool() {
  local value="${1:-}"
  local normalized
  normalized="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
  case "${normalized}" in
    true|1|yes|y) echo "true" ;;
    false|0|no|n) echo "false" ;;
    *) echo "[error] Invalid boolean: ${value}" >&2; exit 1 ;;
  esac
}

PROJECT=""
REGION="us-central1"
ARTIFACT_REPO="echo-ai"
IMAGE_NAME="echoprime-gpu"
IMAGE_URI_OVERRIDE=""
GCS_INPUT_PREFIX=""
GCS_WEIGHTS_PREFIX=""
GCS_OUTPUT_PREFIX=""
MACHINE_TYPE="g2-standard-4"
ACCELERATOR_TYPE="NVIDIA_L4"
ACCELERATOR_COUNT=1
BATCH_SIZE=8
MAX_CLIPS=0
DEVICE="cuda"
BUILD_IMAGE="true"
SERVICE_ACCOUNT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --artifact-repo) ARTIFACT_REPO="$2"; shift 2 ;;
    --image-name) IMAGE_NAME="$2"; shift 2 ;;
    --image-uri) IMAGE_URI_OVERRIDE="$2"; shift 2 ;;
    --gcs-input-prefix) GCS_INPUT_PREFIX="$2"; shift 2 ;;
    --gcs-weights-prefix) GCS_WEIGHTS_PREFIX="$2"; shift 2 ;;
    --gcs-output-prefix) GCS_OUTPUT_PREFIX="$2"; shift 2 ;;
    --machine-type) MACHINE_TYPE="$2"; shift 2 ;;
    --accelerator-type) ACCELERATOR_TYPE="$2"; shift 2 ;;
    --accelerator-count) ACCELERATOR_COUNT="$2"; shift 2 ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --max-clips) MAX_CLIPS="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --build-image) BUILD_IMAGE="$(to_bool "$2")"; shift 2 ;;
    --service-account) SERVICE_ACCOUNT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${PROJECT}" || -z "${GCS_INPUT_PREFIX}" || -z "${GCS_WEIGHTS_PREFIX}" || -z "${GCS_OUTPUT_PREFIX}" ]]; then
  echo "[error] --project, --gcs-input-prefix, --gcs-weights-prefix, and --gcs-output-prefix are required." >&2
  usage
  exit 1
fi
if [[ "${BUILD_IMAGE}" == "false" && -z "${IMAGE_URI_OVERRIDE}" ]]; then
  echo "[error] --image-uri is required when --build-image=false." >&2
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
if [[ -n "${IMAGE_URI_OVERRIDE}" ]]; then
  IMAGE_URI="${IMAGE_URI_OVERRIDE}"
else
  IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT}/${ARTIFACT_REPO}/${IMAGE_NAME}:${TIMESTAMP}"
fi
JOB_NAME="echoprime-embed-${TIMESTAMP}"
JOB_JSON="outputs/vertex_jobs/${JOB_NAME}.json"
mkdir -p "$(dirname "${JOB_JSON}")"

if ! gcloud artifacts repositories describe "${ARTIFACT_REPO}" \
  --project "${PROJECT}" \
  --location "${REGION}" >/dev/null 2>&1; then
  echo "[info] Creating Artifact Registry repo: ${ARTIFACT_REPO}"
  gcloud artifacts repositories create "${ARTIFACT_REPO}" \
    --project "${PROJECT}" \
    --location "${REGION}" \
    --repository-format docker \
    --description "ECHO AI containers"
fi

if [[ "${BUILD_IMAGE}" == "true" && -z "${IMAGE_URI_OVERRIDE}" ]]; then
  echo "[info] Building and pushing image: ${IMAGE_URI}"
  BUILD_CONFIG="$(mktemp /tmp/echo_ai_cloudbuild.XXXXXX)"
  cat > "${BUILD_CONFIG}" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args:
      - build
      - -f
      - docker/Dockerfile.gpu
      - -t
      - ${IMAGE_URI}
      - .
images:
  - ${IMAGE_URI}
EOF
  gcloud builds submit . \
    --project "${PROJECT}" \
    --region "${REGION}" \
    --config "${BUILD_CONFIG}"
  rm -f "${BUILD_CONFIG}"
else
  echo "[info] Skipping image build. Using image: ${IMAGE_URI}"
fi

WORKER_SPEC="replica-count=1,machine-type=${MACHINE_TYPE},container-image-uri=${IMAGE_URI},accelerator-type=${ACCELERATOR_TYPE},accelerator-count=${ACCELERATOR_COUNT}"
ARGS_CSV="--gcs-input-prefix=${GCS_INPUT_PREFIX},--gcs-weights-prefix=${GCS_WEIGHTS_PREFIX},--gcs-output-prefix=${GCS_OUTPUT_PREFIX},--device=${DEVICE},--batch-size=${BATCH_SIZE},--max-clips=${MAX_CLIPS}"

CREATE_CMD=(
  gcloud ai custom-jobs create
  --project "${PROJECT}"
  --region "${REGION}"
  --display-name "${JOB_NAME}"
  --worker-pool-spec "${WORKER_SPEC}"
  --command "/workspace/scripts/run_vertex_echoprime_embedding_job.sh"
  "--args=${ARGS_CSV}"
  --format json
)

if [[ -n "${SERVICE_ACCOUNT}" ]]; then
  CREATE_CMD+=(--service-account "${SERVICE_ACCOUNT}")
fi

echo "[info] Submitting Vertex custom job: ${JOB_NAME}"
TMP_JSON="$(mktemp /tmp/vertex_job_create.XXXXXX)"
if ! "${CREATE_CMD[@]}" > "${TMP_JSON}"; then
  rm -f "${TMP_JSON}"
  exit 1
fi
mv "${TMP_JSON}" "${JOB_JSON}"

JOB_RESOURCE_NAME="$(python3 - <<'PY' "${JOB_JSON}"
import json
import sys
from pathlib import Path

obj = json.loads(Path(sys.argv[1]).read_text())
print(obj.get("name", ""))
PY
)"

echo "[done] Submitted Vertex custom job."
echo "[done] Job resource: ${JOB_RESOURCE_NAME}"
echo "[done] Job JSON: ${JOB_JSON}"
echo "[done] Image URI: ${IMAGE_URI}"
