#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

usage() {
  cat <<'EOF'
Submit Phase 2 reviewer follow-up analyses as an SCC batch job.

This wrapper avoids SCC login-node interactive CPU limits. It submits aggregate-only
follow-up analyses for:
  - leakage-safe non-image baselines,
  - stable-v2 aggregate verifier refresh for TAPSE <17 mm binary summaries,
  - aggregate-only Doppler/M-mode retention audit.

Usage:
  ./scripts/scc_submit_phase2_reviewer_followups.sh \
    [--run nonimage|verify|audit|all] \
    [--output-root /restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2] \
    [--fullscale-root outputs/cloud_cohorts/fullscale_all] \
    [--demographics-csv /restricted/project/mimicecho/metadata/approved_demographics.csv] \
    [--h-rt 8:00:00] \
    [--cores 4] \
    [--mem-per-core 4G] \
    [--sge-project mimicecho] \
    [--job-name echo_p2_followups]

No row-level outputs, prediction CSVs, raw embeddings, logs, or restricted manifests
are copied into the repository by this wrapper.
EOF
}

RUN_MODE="all"
OUTPUT_ROOT="/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2"
FULLSCALE_ROOT="outputs/cloud_cohorts/fullscale_all"
DEMOGRAPHICS_CSV=""
H_RT="8:00:00"
CORES="4"
MEM_PER_CORE="4G"
SGE_PROJECT="mimicecho"
JOB_NAME="echo_p2_followups"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run) RUN_MODE="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --fullscale-root) FULLSCALE_ROOT="$2"; shift 2 ;;
    --demographics-csv) DEMOGRAPHICS_CSV="$2"; shift 2 ;;
    --h-rt) H_RT="$2"; shift 2 ;;
    --cores) CORES="$2"; shift 2 ;;
    --mem-per-core) MEM_PER_CORE="$2"; shift 2 ;;
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

case "${RUN_MODE}" in
  nonimage|verify|audit|all) ;;
  *)
    echo "[error] --run must be one of: nonimage, verify, audit, all" >&2
    exit 1
    ;;
esac

PYTHON_BIN="${REPO_ROOT}/.venv-echoprime/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

LOG_DIR="${REPO_ROOT}/outputs/scc_jobs"
mkdir -p "${LOG_DIR}"

JOB_SCRIPT="$(mktemp "${LOG_DIR}/phase2_followups.XXXXXX.sh")"
cat > "${JOB_SCRIPT}" <<EOF
#!/usr/bin/env bash
set -x

cd "${REPO_ROOT}"

set +eu
for init_file in \\
    /etc/profile.d/modules.sh \\
    /etc/profile \\
    /etc/bashrc \\
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

PYTHON_BIN="${PYTHON_BIN}"
OUT="${OUTPUT_ROOT}"
FULLSCALE_ROOT="${FULLSCALE_ROOT}"
RUN_MODE="${RUN_MODE}"
DEMOGRAPHICS_CSV="${DEMOGRAPHICS_CSV}"

mkdir -p "\${OUT}/review_packets"

if [[ "\${RUN_MODE}" == "nonimage" || "\${RUN_MODE}" == "all" ]]; then
  echo "=== Phase 2 reviewer follow-up: non-image baselines ==="
  DEMO_ARGS=()
  if [[ -n "\${DEMOGRAPHICS_CSV}" ]]; then
    DEMO_ARGS=(--demographics-csv "\${DEMOGRAPHICS_CSV}")
  fi
  "\${PYTHON_BIN}" scripts/run_phase2_nonimage_baselines.py \\
    --output-root "\${OUT}" \\
    --structured-measurements-csv "\${FULLSCALE_ROOT}/manifests/structured_measurements.csv" \\
    --study-embedding-npz "\${FULLSCALE_ROOT}/study_embeddings_512/study_embeddings_512.npz" \\
    --study-embedding-manifest "\${FULLSCALE_ROOT}/study_embeddings_512/study_embedding_manifest.csv" \\
    --subject-split-map-csv "\${FULLSCALE_ROOT}/manifests/subject_split_map_v1.csv" \\
    --selected-studies-csv "\${FULLSCALE_ROOT}/manifests/all_eligible_studies.csv" \\
    --targets lvot_vti,tapse \\
    --aggregate-only \\
    "\${DEMO_ARGS[@]}"
fi

if [[ "\${RUN_MODE}" == "verify" || "\${RUN_MODE}" == "all" ]]; then
  echo "=== Phase 2 reviewer follow-up: aggregate verifier refresh ==="
  REVIEW="\${OUT}/review_packets/phase2_verified_aggregate_only"
  mkdir -p "\${REVIEW}"
  "\${PYTHON_BIN}" scripts/summarize_phase2_imaging_results.py \\
    --output-root "\${OUT}" \\
    --output-dir "\${REVIEW}" \\
    --draft-md docs/phase2_results_manuscript_draft.md \\
    --write-markdown-summary
fi

if [[ "\${RUN_MODE}" == "audit" || "\${RUN_MODE}" == "all" ]]; then
  echo "=== Phase 2 reviewer follow-up: Doppler/M-mode retention audit ==="
  DOPPLER_AUDIT="\${OUT}/review_packets/phase2_doppler_mmode_retention_audit"
  mkdir -p "\${DOPPLER_AUDIT}"
  "\${PYTHON_BIN}" scripts/audit_phase2_doppler_mmode_retention.py \\
    --output-root "\${OUT}" \\
    --fullscale-root "\${FULLSCALE_ROOT}" \\
    --output-dir "\${DOPPLER_AUDIT}" \\
    --phase2-output-root "\${OUT}" \\
    --aggregate-only
fi

echo "[done] Phase 2 reviewer follow-up job complete."
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
  -l "h_rt=${H_RT}" \
  -pe omp "${CORES}" \
  -l "mem_per_core=${MEM_PER_CORE}" \
  "${JOB_SCRIPT}")"

echo "${QSUB_OUT}"
echo "[info] Job script: ${JOB_SCRIPT}"
echo "[info] Monitor with: qstat -u \$(whoami)"
echo "[info] Log will appear in: ${LOG_DIR}/${JOB_NAME}.o*"
