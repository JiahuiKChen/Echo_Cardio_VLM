#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${REPO_ROOT}/outputs/scc_jobs"
mkdir -p "${LOG_DIR}"

usage() {
  cat <<'EOF'
Submit EchoPrime embedding extraction + baseline as an SCC batch job.

Prerequisites:
  1) source scc_env.sh
  2) EchoPrime weights staged (run scc_stage_echoprime_weights.sh first)
  3) Stage D cohort completed

Usage:
  ./scripts/scc_submit_echoprime_embedding_job.sh \
    [--cohort-root outputs/cloud_cohorts/stage_d_500study_scc] \
    [--weights-dir /restricted/project/mimicecho/echoprime_weights] \
    [--device auto] \
    [--batch-size 8] \
    [--sge-project mimicecho] \
    [--job-name echo_emb] \
    [--gpu true|false]
EOF
}

to_bool() {
  local value="${1:-}"
  local normalized
  normalized="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
  case "${normalized}" in
    true|1|yes|y) echo "true" ;;
    false|0|no|n) echo "false" ;;
    *)
      echo "[error] Invalid boolean value: ${value}" >&2
      exit 1
      ;;
  esac
}

COHORT_ROOT="${REPO_ROOT}/outputs/cloud_cohorts/stage_d_500study_scc"
WEIGHTS_DIR="${ECHO_AI_RESTRICTED_PROJECT:-/restricted/project/mimicecho}/echoprime_weights"
DEVICE="auto"
BATCH_SIZE=8
SGE_PROJECT="mimicecho"
JOB_NAME="echo_emb"
USE_GPU="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cohort-root) COHORT_ROOT="$2"; shift 2 ;;
    --weights-dir) WEIGHTS_DIR="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --sge-project) SGE_PROJECT="$2"; shift 2 ;;
    --job-name) JOB_NAME="$2"; shift 2 ;;
    --gpu) USE_GPU="$(to_bool "$2")"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

PYTHON_BIN="${REPO_ROOT}/.venv-echoprime/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

JOB_SCRIPT="$(mktemp "${LOG_DIR}/echoprime_emb.XXXXXX.sh")"
cat > "${JOB_SCRIPT}" <<EOF
#!/usr/bin/env bash
set -x

cd "${REPO_ROOT}"

set +eu
for init_file in \
    /etc/profile.d/modules.sh \
    /etc/profile \
    /etc/bashrc \
    /usr/share/Modules/init/bash; do
  if command -v module >/dev/null 2>&1; then
    break
  fi
  if [[ -f "\${init_file}" ]]; then
    source "\${init_file}" >/dev/null 2>&1 || true
  fi
done
set -euo pipefail

if command -v module >/dev/null 2>&1; then
  module load python3/3.10.12
fi

if [[ -f ./scc_env.sh ]]; then
  source ./scc_env.sh
fi

${PYTHON_BIN} -c "import torch; print('CUDA:', torch.cuda.is_available()); print('Device count:', torch.cuda.device_count() if torch.cuda.is_available() else 0)"

./scripts/run_echoprime_embedding_pipeline.sh \
  --cohort-root "${COHORT_ROOT}" \
  --weights-dir "${WEIGHTS_DIR}" \
  --python-bin "${PYTHON_BIN}" \
  --device "${DEVICE}" \
  --batch-size "${BATCH_SIZE}"
EOF
chmod +x "${JOB_SCRIPT}"

if ! command -v qsub >/dev/null 2>&1; then
  echo "[error] qsub not found. Run this script from an SCC login node." >&2
  exit 1
fi

GPU_FLAG=""
if [[ "${USE_GPU}" == "true" ]]; then
  GPU_FLAG="-l gpus=1 -l gpu_c=7.0"
fi

QSUB_OUT="$(qsub \
  -cwd \
  -P "${SGE_PROJECT}" \
  -N "${JOB_NAME}" \
  -j y \
  -o "${LOG_DIR}" \
  -l h_rt=4:00:00 \
  -pe omp 4 \
  -l mem_per_core=4G \
  ${GPU_FLAG} \
  "${JOB_SCRIPT}")"
echo "${QSUB_OUT}"
echo "[info] Job script: ${JOB_SCRIPT}"
echo "[info] Monitor with: qstat -u \$(whoami)"
echo "[info] Log will appear in: ${LOG_DIR}/"
