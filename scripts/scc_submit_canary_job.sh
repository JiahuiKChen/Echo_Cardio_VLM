#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${REPO_ROOT}/outputs/scc_jobs"
mkdir -p "${LOG_DIR}"

usage() {
  cat <<'EOF'
Submit SCC canary batch job (2-study end-to-end run).

Usage:
  ./scripts/scc_submit_canary_job.sh \
    [--billing-project mimic-iv-anesthesia] \
    [--sge-project mimicecho] \
    [--job-name echo_canary2]
EOF
}

BILLING_PROJECT="${ECHO_AI_BILLING_PROJECT:-mimic-iv-anesthesia}"
SGE_PROJECT="mimicecho"
JOB_NAME="echo_canary2"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --billing-project) BILLING_PROJECT="$2"; shift 2 ;;
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

JOB_SCRIPT="$(mktemp "${LOG_DIR}/canary_2study.XXXXXX.sh")"
cat > "${JOB_SCRIPT}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
set -x
cd "${REPO_ROOT}"

if [[ -f /etc/profile.d/modules.sh ]]; then
  source /etc/profile.d/modules.sh >/dev/null 2>&1 || true
fi
if ! command -v module >/dev/null 2>&1 && [[ -f /etc/profile ]]; then
  source /etc/profile >/dev/null 2>&1 || true
fi
if ! command -v module >/dev/null 2>&1 && [[ -f /etc/bashrc ]]; then
  source /etc/bashrc >/dev/null 2>&1 || true
fi
if ! command -v module >/dev/null 2>&1 && [[ -f /usr/share/Modules/init/bash ]]; then
  source /usr/share/Modules/init/bash >/dev/null 2>&1 || true
fi

if ! command -v module >/dev/null 2>&1; then
  echo "[error] Could not initialize SCC module command in batch shell." >&2
  exit 127
fi

module load python3/3.10.12
module load google-cloud-sdk/455.0.0

if [[ -f ./scc_env.sh ]]; then
  source ./scc_env.sh
else
  echo "[warn] ./scc_env.sh not found; relying on existing environment variables."
fi

./scripts/scc_run_canary_2study.sh --billing-project "${BILLING_PROJECT}"
EOF
chmod +x "${JOB_SCRIPT}"

if ! command -v qsub >/dev/null 2>&1; then
  echo "[error] qsub not found. Run this script from an SCC login node." >&2
  exit 1
fi

QSUB_OUT="$(qsub -cwd -P "${SGE_PROJECT}" -N "${JOB_NAME}" -j y -o "${LOG_DIR}" "${JOB_SCRIPT}")"
echo "${QSUB_OUT}"
echo "[info] Job script: ${JOB_SCRIPT}"
