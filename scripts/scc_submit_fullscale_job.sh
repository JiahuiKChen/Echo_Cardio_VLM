#!/usr/bin/env bash
set -euo pipefail
#
# Submit the full-scale batch-and-purge pipeline as an SCC GPU job.
#
# Runtime estimate: ~14 batches × ~2h each = ~28h total
# Storage: peaks at ~165 GB per batch, purged between batches
#
# Usage:  ./scripts/scc_submit_fullscale_job.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

JOB_DIR="${REPO_ROOT}/outputs/scc_jobs"
mkdir -p "${JOB_DIR}"

TMPSCRIPT="$(mktemp "${JOB_DIR}/fullscale_XXXXXX.sh")"

cat > "${TMPSCRIPT}" <<'JOBEOF'
#!/bin/bash -l
#$ -P mimicecho
#$ -N echo_fullscale
#$ -j y
#$ -m ea

set +eu
for init_file in /etc/profile.d/modules.sh /etc/profile /etc/bashrc /usr/share/Modules/init/bash; do
  command -v module &>/dev/null && break
  [[ -f "${init_file}" ]] && source "${init_file}"
done
set -euo pipefail

command -v module && module load python3/3.10.12
command -v module && module load google-cloud-sdk/455.0.0

cd __REPO_ROOT__
[[ -f ./scc_env.sh ]] && source ./scc_env.sh

__REPO_ROOT__/.venv-echoprime/bin/python -c \
  'import torch; print("CUDA:", torch.cuda.is_available(), "Device:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none")'

./scripts/scc_run_fullscale_pipeline.sh
JOBEOF

sed -i "s|__REPO_ROOT__|${REPO_ROOT}|g" "${TMPSCRIPT}"

echo "[info] Full-scale job script: ${TMPSCRIPT}"
echo "[info] Submitting to SCC batch system..."

qsub \
  -o "${JOB_DIR}" \
  -l h_rt=48:00:00 \
  -l gpus=1 \
  -l gpu_c=8.0 \
  -l gpu_memory=48G \
  -pe omp 4 \
  -l mem_per_core=4G \
  "${TMPSCRIPT}"

echo "[done] Job submitted. Monitor with: qstat -u \$(whoami)"
echo "[done] Log will appear in: ${JOB_DIR}/echo_fullscale.o*"
echo ""
echo "Pipeline processes ~14 batches of 500 studies each."
echo "Each batch: download → extract → embed → purge."
echo "Estimated runtime: 24-36 hours."
echo ""
echo "To monitor progress:"
echo "  tail -f ${JOB_DIR}/echo_fullscale.o*"
echo "  ls ${REPO_ROOT}/outputs/cloud_cohorts/fullscale_all/batches/*_embeddings/"
