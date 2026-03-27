#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Prepare SCC workspace for ECHO AI project.

Usage:
  ./scripts/scc_prepare_workspace.sh \
    [--project-name mimicecho] \
    [--billing-project mimic-iv-anesthesia] \
    [--python-bin python3] \
    [--setup-env true|false] \
    [--run-preflight true|false]

Outputs:
  - Creates directory tree under SCC project storage (/restricted/* or /project* paths).
  - Writes scc_env.sh at repo root with standardized environment exports.
  - Optionally sets up .venv-echoprime and runs data-access preflight.
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

PROJECT_NAME="mimicecho"
BILLING_PROJECT="${ECHO_AI_BILLING_PROJECT:-}"
PYTHON_BIN="python3"
SETUP_ENV="true"
RUN_PREFLIGHT="false"

resolve_project_dir() {
  local kind="$1" # project | projectnb
  local name="$2"
  local candidate=""

  for p in "/restricted/${kind}/${name}" "/${kind}/${name}"; do
    if [[ -d "${p}" ]]; then
      candidate="${p}"
      break
    fi
  done

  if [[ -z "${candidate}" ]]; then
    for base in "/restricted/${kind}" "/${kind}"; do
      if [[ -d "${base}" ]] && mkdir -p "${base}/${name}" 2>/dev/null; then
        candidate="${base}/${name}"
        break
      fi
    done
  fi

  if [[ -z "${candidate}" ]]; then
    echo "[error] Could not access or create shared ${kind} storage for project '${name}'." >&2
    echo "[error] Checked: /${kind}/${name} and /restricted/${kind}/${name}" >&2
    echo "[error] This is an SCC provisioning/permission blocker. Do not proceed with local/home fallback for this project." >&2
    echo "[error] Ask SCC to provision writable directories for group '${name}' in /project and /projectnb (or /restricted aliases)." >&2
    exit 1
  fi

  echo "${candidate}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-name) PROJECT_NAME="$2"; shift 2 ;;
    --billing-project) BILLING_PROJECT="$2"; shift 2 ;;
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --setup-env) SETUP_ENV="$(to_bool "$2")"; shift 2 ;;
    --run-preflight) RUN_PREFLIGHT="$(to_bool "$2")"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

PROJECT_PATH="$(resolve_project_dir "project" "${PROJECT_NAME}")"
PROJECTNB_PATH="$(resolve_project_dir "projectnb" "${PROJECT_NAME}")"
DATA_ROOT="${PROJECTNB_PATH}/echo_ai_data"

echo "[info] Repo root: ${REPO_ROOT}"
echo "[info] Project path: ${PROJECT_PATH}"
echo "[info] Projectnb path: ${PROJECTNB_PATH}"
echo "[info] Data root: ${DATA_ROOT}"

mkdir -p "${PROJECT_PATH}/code"
mkdir -p "${PROJECT_PATH}/outputs"
mkdir -p "${PROJECT_PATH}/logs"
mkdir -p "${DATA_ROOT}/cloud_cohorts"
mkdir -p "${DATA_ROOT}/tmp"

ENV_FILE="${REPO_ROOT}/scc_env.sh"
cat > "${ENV_FILE}" <<EOF
#!/usr/bin/env bash
export ECHO_AI_PROJECT_NAME="${PROJECT_NAME}"
export ECHO_AI_RESTRICTED_PROJECT="${PROJECT_PATH}"
export ECHO_AI_RESTRICTED_PROJECTNB="${PROJECTNB_PATH}"
export ECHO_AI_DATA_ROOT="${DATA_ROOT}"
export ECHO_AI_BILLING_PROJECT="${BILLING_PROJECT}"
EOF
chmod 600 "${ENV_FILE}"
echo "[written] ${ENV_FILE}"

if [[ "${SETUP_ENV}" == "true" ]]; then
  echo "[info] Setting up EchoPrime Python environment..."
  PYTHON_BIN="${PYTHON_BIN}" "${SCRIPT_DIR}/setup_echoprime_env.sh"
fi

if [[ "${RUN_PREFLIGHT}" == "true" ]]; then
  if [[ -z "${BILLING_PROJECT}" ]]; then
    echo "[error] --run-preflight=true requires --billing-project." >&2
    exit 1
  fi
  echo "[info] Running cloud/data preflight checks..."
  "${SCRIPT_DIR}/preflight_data_access.sh" \
    --billing-project "${BILLING_PROJECT}" \
    --bucket-primary "mimic-iv-echo-1.0.physionet.org" \
    --output-dir "${PROJECT_PATH}/outputs/access_preflight"
fi

echo "[done] SCC workspace preparation complete."
echo "[next] source ${ENV_FILE}"
