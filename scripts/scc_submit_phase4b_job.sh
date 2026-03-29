#!/usr/bin/env bash
set -euo pipefail
#
# Phase 4b: Multi-video extraction, embedding, aggregation, and baseline.
#
# Step 1 (CPU):  Extract ALL cine clips per study (no per-study limit)
# Step 2 (GPU):  Compute 512-d EchoPrime encoder embeddings (encoder-only, no view classifier)
# Step 3 (CPU):  Aggregate clip embeddings to study-level (mean pooling)
# Step 4 (CPU):  Run LVEF baseline on study-level embeddings
#
# Submit: ./scripts/scc_submit_phase4b_job.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

COHORT_ROOT="${REPO_ROOT}/outputs/cloud_cohorts/stage_d_500study_scc"
DOWNLOAD_ROOT="${ECHO_AI_DATA_ROOT:-/restricted/projectnb/mimicecho/echo_ai_data}/cloud_cohorts/stage_d_500study_scc"
WEIGHTS_DIR="/restricted/project/mimicecho/echoprime_weights"
PYTHON_BIN="${REPO_ROOT}/.venv-echoprime/bin/python"
BATCH_SIZE=8
NUM_WORKERS=4

JOB_DIR="${REPO_ROOT}/outputs/scc_jobs"
mkdir -p "${JOB_DIR}"

TMPSCRIPT="$(mktemp "${JOB_DIR}/phase4b_XXXXXX.sh")"

cat > "${TMPSCRIPT}" <<'JOBEOF'
#!/bin/bash -l
#$ -P mimicecho
#$ -N echo_phase4b
#$ -j y
#$ -m ea

set +eu
for init_file in /etc/profile.d/modules.sh /etc/profile /etc/bashrc /usr/share/Modules/init/bash; do
  command -v module &>/dev/null && break
  [[ -f "${init_file}" ]] && source "${init_file}"
done
set -euo pipefail

command -v module && module load python3/3.10.12

cd __REPO_ROOT__
[[ -f ./scc_env.sh ]] && source ./scc_env.sh

PYTHON_BIN="__PYTHON_BIN__"
COHORT_ROOT="__COHORT_ROOT__"
DOWNLOAD_ROOT="__DOWNLOAD_ROOT__"
WEIGHTS_DIR="__WEIGHTS_DIR__"
BATCH_SIZE="__BATCH_SIZE__"
NUM_WORKERS="__NUM_WORKERS__"

AUDIT_CSV="${COHORT_ROOT}/audit/cine_candidates.csv"
ALLCLIP_OUTPUT_ROOT="${DOWNLOAD_ROOT}/derived/allclip_npz"
ALLCLIP_MANIFEST="${COHORT_ROOT}/extract_allclip/extraction_manifest.csv"
EMB_DIR="${COHORT_ROOT}/echoprime_embeddings_512"
EMB_NPZ="${EMB_DIR}/clip_embeddings_512.npz"
EMB_MANIFEST="${EMB_DIR}/clip_embedding_manifest.csv"
STUDY_EMB_DIR="${COHORT_ROOT}/study_embeddings_512"
STUDY_EMB_NPZ="${STUDY_EMB_DIR}/study_embeddings_512.npz"
STUDY_EMB_MANIFEST="${STUDY_EMB_DIR}/study_embedding_manifest.csv"
BASELINE_DIR="${COHORT_ROOT}/baseline_lvef_study_embeddings"
LVEF_MANIFEST="${COHORT_ROOT}/manifests/lvef_still_manifest.csv"

mkdir -p "$(dirname "${ALLCLIP_MANIFEST}")" "${EMB_DIR}" "${STUDY_EMB_DIR}" "${BASELINE_DIR}"

echo "=== Phase 4b Step 1: Extract ALL cine clips (no per-study limit) ==="
"${PYTHON_BIN}" scripts/extract_mimic_echo_cines.py \
  --audit-csv "${AUDIT_CSV}" \
  --data-root "${DOWNLOAD_ROOT}" \
  --output-root "${ALLCLIP_OUTPUT_ROOT}" \
  --output-manifest "${ALLCLIP_MANIFEST}" \
  --max-clips 0 \
  --max-clips-per-study 0 \
  --num-workers "${NUM_WORKERS}" \
  --progress-every 1000

echo "=== Phase 4b Step 2: Extract 512-d encoder embeddings (encoder-only) ==="
"${PYTHON_BIN}" scripts/extract_echoprime_embeddings.py \
  --extraction-manifest "${ALLCLIP_MANIFEST}" \
  --weights-dir "${WEIGHTS_DIR}" \
  --output-npz "${EMB_NPZ}" \
  --output-manifest "${EMB_MANIFEST}" \
  --device auto \
  --batch-size "${BATCH_SIZE}" \
  --encoder-only \
  --checkpoint-every 2000

echo "=== Phase 4b Step 3: Aggregate to study-level embeddings ==="
"${PYTHON_BIN}" scripts/aggregate_study_embeddings.py \
  --embedding-npz "${EMB_NPZ}" \
  --embedding-manifest "${EMB_MANIFEST}" \
  --output-npz "${STUDY_EMB_NPZ}" \
  --output-manifest "${STUDY_EMB_MANIFEST}" \
  --method mean

echo "=== Phase 4b Step 4: Run LVEF baseline on study-level embeddings ==="
"${PYTHON_BIN}" scripts/run_echoprime_embedding_baseline.py \
  --embedding-npz "${STUDY_EMB_NPZ}" \
  --embedding-manifest "${STUDY_EMB_MANIFEST}" \
  --label-manifest "${LVEF_MANIFEST}" \
  --output-dir "${BASELINE_DIR}" \
  --join-key study_id

echo "[done] Phase 4b complete."
JOBEOF

sed -i "s|__REPO_ROOT__|${REPO_ROOT}|g"     "${TMPSCRIPT}"
sed -i "s|__PYTHON_BIN__|${PYTHON_BIN}|g"   "${TMPSCRIPT}"
sed -i "s|__COHORT_ROOT__|${COHORT_ROOT}|g"  "${TMPSCRIPT}"
sed -i "s|__DOWNLOAD_ROOT__|${DOWNLOAD_ROOT}|g" "${TMPSCRIPT}"
sed -i "s|__WEIGHTS_DIR__|${WEIGHTS_DIR}|g"  "${TMPSCRIPT}"
sed -i "s|__BATCH_SIZE__|${BATCH_SIZE}|g"    "${TMPSCRIPT}"
sed -i "s|__NUM_WORKERS__|${NUM_WORKERS}|g"  "${TMPSCRIPT}"

echo "[info] Phase 4b job script: ${TMPSCRIPT}"
echo "[info] Submitting to SCC batch system..."

qsub \
  -o "${JOB_DIR}" \
  -l h_rt=8:00:00 \
  -l gpus=1 \
  -l gpu_c=8.0 \
  -l gpu_memory=48G \
  -pe omp "${NUM_WORKERS}" \
  -l mem_per_core=4G \
  "${TMPSCRIPT}"

echo "[done] Job submitted. Monitor with: qstat -u \$(whoami)"
echo "[done] Log will appear in: ${JOB_DIR}/echo_phase4b.o*"
