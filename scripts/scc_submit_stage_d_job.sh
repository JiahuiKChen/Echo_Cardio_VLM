#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${REPO_ROOT}/outputs/scc_jobs"
mkdir -p "${LOG_DIR}"

usage() {
  cat <<'EOF'
Submit Stage D SCC batch job.

Usage:
  ./scripts/scc_submit_stage_d_job.sh \
    [--billing-project mimic-iv-anesthesia] \
    [--run-baseline true|false] \
    [--extract-max-clips 1000] \
    [--extract-max-clips-per-study 2] \
    [--sge-project mimicecho] \
    [--job-name echo_stage_d]
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

BILLING_PROJECT="${ECHO_AI_BILLING_PROJECT:-mimic-iv-anesthesia}"
RUN_BASELINE="false"
EXTRACT_MAX_CLIPS=1000
EXTRACT_MAX_CLIPS_PER_STUDY=2
EXTRACT_NUM_WORKERS=4
SGE_PROJECT="mimicecho"
JOB_NAME="echo_stage_d"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --billing-project) BILLING_PROJECT="$2"; shift 2 ;;
    --run-baseline) RUN_BASELINE="$(to_bool "$2")"; shift 2 ;;
    --extract-max-clips) EXTRACT_MAX_CLIPS="$2"; shift 2 ;;
    --extract-max-clips-per-study) EXTRACT_MAX_CLIPS_PER_STUDY="$2"; shift 2 ;;
    --extract-num-workers) EXTRACT_NUM_WORKERS="$2"; shift 2 ;;
    --sge-project) SGE_PROJECT="$2"; shift 2 ;;
    --job-name) JOB_NAME="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

JOB_SCRIPT="$(mktemp "${LOG_DIR}/stage_d_500study.XXXXXX.sh")"
cat > "${JOB_SCRIPT}" <<EOF
#!/usr/bin/env bash
set -x

cd "${REPO_ROOT}"

# Module init requires relaxed shell — profile scripts reference unset vars
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
set -eu

if ! command -v module >/dev/null 2>&1; then
  echo "[error] Could not initialize SCC module command in batch shell." >&2
  exit 127
fi

module load python3/3.10.12
module load google-cloud-sdk/455.0.0

set -o pipefail

if [[ -f ./scc_env.sh ]]; then
  source ./scc_env.sh
else
  echo "[warn] ./scc_env.sh not found; relying on existing environment variables."
fi

./scripts/scc_run_stage_d_500study.sh \
  --billing-project "${BILLING_PROJECT}" \
  --run-baseline "${RUN_BASELINE}" \
  --extract-max-clips "${EXTRACT_MAX_CLIPS}" \
  --extract-max-clips-per-study "${EXTRACT_MAX_CLIPS_PER_STUDY}" \
  --extract-num-workers "${EXTRACT_NUM_WORKERS}"
EOF
chmod +x "${JOB_SCRIPT}"

if ! command -v qsub >/dev/null 2>&1; then
  echo "[error] qsub not found. Run this script from an SCC login node." >&2
  exit 1
fi

QSUB_OUT="$(qsub \
  -cwd \
  -P "${SGE_PROJECT}" \
  -N "${JOB_NAME}" \
  -j y \
  -o "${LOG_DIR}" \
  -l h_rt=6:00:00 \
  -pe omp "${EXTRACT_NUM_WORKERS}" \
  -l mem_per_core=4G \
  "${JOB_SCRIPT}")"
echo "${QSUB_OUT}"
echo "[info] Job script: ${JOB_SCRIPT}"
echo "[info] Monitor with: qstat -u \$(whoami)"
echo "[info] Log will appear in: ${LOG_DIR}/"
