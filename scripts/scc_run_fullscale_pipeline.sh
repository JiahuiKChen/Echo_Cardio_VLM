#!/usr/bin/env bash
set -euo pipefail
#
# Phase 6: Full-scale batch-and-purge pipeline.
#
# Selects ALL eligible MIMIC-IV-ECHO studies via BigQuery, splits into batches
# of ~500, and for each batch: downloads DICOMs, extracts all cine clips,
# computes 512-d encoder-only embeddings, then purges raw DICOMs and clip NPZs
# to stay within the 800 GB projectnb quota.
#
# After all batches: merges embeddings with the existing 500-study results,
# aggregates to study-level, exports measurements, and runs the evaluation ladder.
#
# Expects: source scc_env.sh, modules loaded, GPU available.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

BILLING_PROJECT="${ECHO_AI_BILLING_PROJECT:-mimic-iv-anesthesia}"
BQ_PROJECT="physionet-data"
BQ_DATASET="mimiciv_echo"
GCS_BUCKET="mimic-iv-echo-1.0.physionet.org"
PYTHON_BIN="${REPO_ROOT}/.venv-echoprime/bin/python"
WEIGHTS_DIR="/restricted/project/mimicecho/echoprime_weights"
DATA_ROOT="${ECHO_AI_DATA_ROOT:-/restricted/projectnb/mimicecho/echo_ai_data}"
BATCH_SIZE_STUDIES=500
EMBED_BATCH_SIZE=8
EXTRACT_NUM_WORKERS=4
MIN_DICOMS=5
MAX_DICOMS=99999
SEED=20260323

FULLSCALE_ROOT="${REPO_ROOT}/outputs/cloud_cohorts/fullscale_all"
EXISTING_EMB_DIR="${REPO_ROOT}/outputs/cloud_cohorts/stage_d_500study_scc/echoprime_embeddings_512"
EXISTING_STUDIES_CSV="${REPO_ROOT}/outputs/cloud_cohorts/stage_d_500study_scc/manifests/selected_studies.csv"

mkdir -p "${FULLSCALE_ROOT}/manifests" "${FULLSCALE_ROOT}/queries" "${FULLSCALE_ROOT}/batches"

log() { echo "[$(date -u +%FT%T)] $*"; }

# ===================================================================
# Step 1: Select ALL eligible studies from BigQuery
# ===================================================================
ALL_STUDIES_CSV="${FULLSCALE_ROOT}/manifests/all_eligible_studies.csv"

if [[ -f "${ALL_STUDIES_CSV}" ]] && [[ "$(wc -l < "${ALL_STUDIES_CSV}")" -gt 100 ]]; then
  log "Reusing existing study selection: ${ALL_STUDIES_CSV}"
else
  log "=== Step 1: Selecting all eligible studies from BigQuery ==="
  STUDIES_SQL="${FULLSCALE_ROOT}/queries/select_all_studies.sql"
  cat > "${STUDIES_SQL}" <<EOSQL
WITH per_study AS (
  SELECT
    subject_id,
    study_id,
    COUNT(*) AS n_dicoms,
    MIN(acquisition_datetime) AS first_acquisition_datetime,
    MAX(acquisition_datetime) AS last_acquisition_datetime
  FROM \`${BQ_PROJECT}.${BQ_DATASET}.echo_record_list\`
  GROUP BY 1, 2
),
base AS (
  SELECT
    p.*,
    s.study_datetime,
    s.measurement_id,
    s.measurement_datetime,
    s.note_id,
    s.note_seq,
    s.note_charttime
  FROM per_study p
  LEFT JOIN \`${BQ_PROJECT}.${BQ_DATASET}.echo_study_list\` s
    USING (subject_id, study_id)
),
filtered AS (
  SELECT *
  FROM base
  WHERE n_dicoms BETWEEN ${MIN_DICOMS} AND ${MAX_DICOMS}
    AND measurement_id IS NOT NULL
),
ranked AS (
  SELECT
    f.*,
    ROW_NUMBER() OVER (
      PARTITION BY f.subject_id
      ORDER BY
        FARM_FINGERPRINT(CONCAT(CAST(f.study_id AS STRING), '-', CAST(${SEED} AS STRING)))
    ) AS rn_subject
  FROM filtered f
)
SELECT
  subject_id,
  study_id,
  n_dicoms,
  first_acquisition_datetime,
  last_acquisition_datetime,
  study_datetime,
  measurement_id,
  measurement_datetime,
  note_id,
  note_seq,
  note_charttime
FROM ranked
WHERE rn_subject <= 1
ORDER BY
  FARM_FINGERPRINT(CONCAT(CAST(study_id AS STRING), '-', CAST(${SEED} AS STRING)))
EOSQL

  bq --project_id="${BILLING_PROJECT}" query \
    --nouse_legacy_sql \
    --format=csv \
    --max_rows=50000 \
    "$(cat "${STUDIES_SQL}")" > "${ALL_STUDIES_CSV}"

  TOTAL_STUDIES="$(tail -n +2 "${ALL_STUDIES_CSV}" | wc -l | tr -d ' ')"
  log "Selected ${TOTAL_STUDIES} eligible studies"
fi

# ===================================================================
# Step 2: Identify already-processed studies, split remaining into batches
# ===================================================================
log "=== Step 2: Splitting into batches ==="
"${PYTHON_BIN}" - <<'PYEOF'
import pandas as pd
import sys, json
from pathlib import Path

all_csv = Path(sys.argv[1])
existing_csv = Path(sys.argv[2])
batch_dir = Path(sys.argv[3])
batch_size = int(sys.argv[4])

all_df = pd.read_csv(all_csv)
print(f"[info] Total eligible studies: {len(all_df)}")

already_done = set()
if existing_csv.exists():
    existing = pd.read_csv(existing_csv)
    already_done = set(existing["study_id"].tolist())
    print(f"[info] Already processed: {len(already_done)} studies")

remaining = all_df[~all_df["study_id"].isin(already_done)].reset_index(drop=True)
print(f"[info] Remaining to process: {len(remaining)} studies")

n_batches = (len(remaining) + batch_size - 1) // batch_size
batch_dir.mkdir(parents=True, exist_ok=True)

manifest = []
for i in range(n_batches):
    start = i * batch_size
    end = min(start + batch_size, len(remaining))
    batch_df = remaining.iloc[start:end]
    batch_csv = batch_dir / f"batch_{i:03d}_studies.csv"
    batch_df.to_csv(batch_csv, index=False)
    manifest.append({
        "batch_id": i,
        "n_studies": len(batch_df),
        "csv": str(batch_csv),
    })
    print(f"[info] Batch {i:03d}: {len(batch_df)} studies -> {batch_csv.name}")

summary = {"n_total": len(all_df), "n_already_done": len(already_done),
           "n_remaining": len(remaining), "n_batches": n_batches, "batches": manifest}
(batch_dir / "batch_manifest.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
PYEOF
"${ALL_STUDIES_CSV}" "${EXISTING_STUDIES_CSV}" "${FULLSCALE_ROOT}/batches" "${BATCH_SIZE_STUDIES}"

BATCH_MANIFEST="${FULLSCALE_ROOT}/batches/batch_manifest.json"
N_BATCHES="$("${PYTHON_BIN}" -c "import json; print(json.load(open('${BATCH_MANIFEST}'))['n_batches'])")"
log "Total batches to process: ${N_BATCHES}"

# ===================================================================
# Step 3: Process each batch (download → audit → extract → embed → purge)
# ===================================================================
for BATCH_IDX in $(seq 0 $((N_BATCHES - 1))); do
  BATCH_TAG="$(printf 'batch_%03d' "${BATCH_IDX}")"
  BATCH_STUDIES_CSV="${FULLSCALE_ROOT}/batches/${BATCH_TAG}_studies.csv"
  BATCH_EMB_DIR="${FULLSCALE_ROOT}/batches/${BATCH_TAG}_embeddings"
  BATCH_DOWNLOAD_ROOT="${DATA_ROOT}/cloud_cohorts/fullscale_batch_tmp"
  BATCH_AUDIT_DIR="${FULLSCALE_ROOT}/batches/${BATCH_TAG}_audit"
  BATCH_NPZ_ROOT="${DATA_ROOT}/cloud_cohorts/fullscale_batch_tmp/derived/allclip_npz"
  BATCH_EXTRACT_MANIFEST="${FULLSCALE_ROOT}/batches/${BATCH_TAG}_extraction_manifest.csv"

  # Skip if embeddings already exist for this batch
  if [[ -f "${BATCH_EMB_DIR}/clip_embeddings_512.npz" ]]; then
    log "[batch ${BATCH_IDX}/${N_BATCHES}] Already complete, skipping"
    continue
  fi

  log "=== Batch ${BATCH_IDX}/${N_BATCHES}: Processing ${BATCH_TAG} ==="
  mkdir -p "${BATCH_EMB_DIR}" "${BATCH_AUDIT_DIR}" "${BATCH_DOWNLOAD_ROOT}"

  # --- 3a: Download DICOMs ---
  log "[batch ${BATCH_IDX}] Downloading DICOMs..."
  BATCH_RECORDS_SQL="${FULLSCALE_ROOT}/batches/${BATCH_TAG}_records.sql"
  BATCH_RECORDS_CSV="${FULLSCALE_ROOT}/batches/${BATCH_TAG}_records.csv"

  STUDY_IDS="$("${PYTHON_BIN}" -c "
import pandas as pd
df = pd.read_csv('${BATCH_STUDIES_CSV}')
print(','.join(str(x) for x in df['study_id'].tolist()))
")"

  cat > "${BATCH_RECORDS_SQL}" <<EOSQL
SELECT
  r.subject_id,
  r.study_id,
  r.acquisition_datetime,
  r.dicom_filepath
FROM \`${BQ_PROJECT}.${BQ_DATASET}.echo_record_list\` r
WHERE r.study_id IN (${STUDY_IDS})
ORDER BY r.study_id, r.dicom_filepath
EOSQL

  bq --project_id="${BILLING_PROJECT}" query \
    --nouse_legacy_sql --format=csv --max_rows=10000000 \
    "$(cat "${BATCH_RECORDS_SQL}")" > "${BATCH_RECORDS_CSV}"

  log "[batch ${BATCH_IDX}] Downloading from GCS..."
  tail -n +2 "${BATCH_STUDIES_CSV}" | while IFS=, read -r subject_id study_id n_dicoms _; do
    pfx="$(printf 'p%02d' "$((subject_id / 1000000))")"
    parent_dir="${BATCH_DOWNLOAD_ROOT}/files/${pfx}/p${subject_id}"
    study_dir="${parent_dir}/s${study_id}"
    mkdir -p "${parent_dir}"

    existing=0
    if [[ -d "${study_dir}" ]]; then
      existing="$(find "${study_dir}" -maxdepth 1 -type f -name '*.dcm' 2>/dev/null | wc -l | tr -d ' ')"
    fi
    if [[ "${existing}" -ge "${n_dicoms}" ]]; then
      continue
    fi

    src_uri="gs://${GCS_BUCKET}/files/${pfx}/p${subject_id}/s${study_id}"
    for attempt in 1 2 3; do
      if gsutil -u "${BILLING_PROJECT}" -m cp -r "${src_uri}" "${parent_dir}" 2>&1; then
        break
      fi
      find "${study_dir}" -name '*.gstmp' -delete 2>/dev/null || true
      sleep 5
    done
  done

  # --- 3b: DICOM audit ---
  log "[batch ${BATCH_IDX}] Running DICOM audit..."
  "${PYTHON_BIN}" scripts/audit_mimic_echo_dicoms.py \
    --data-root "${BATCH_DOWNLOAD_ROOT}" \
    --records-csv "${BATCH_RECORDS_CSV}" \
    --output-audit "${BATCH_AUDIT_DIR}/dicom_audit.csv" \
    --output-cine "${BATCH_AUDIT_DIR}/cine_candidates.csv"

  # --- 3c: Extract all cine clips ---
  log "[batch ${BATCH_IDX}] Extracting all cine clips..."
  "${PYTHON_BIN}" scripts/extract_mimic_echo_cines.py \
    --audit-csv "${BATCH_AUDIT_DIR}/cine_candidates.csv" \
    --data-root "${BATCH_DOWNLOAD_ROOT}" \
    --output-root "${BATCH_NPZ_ROOT}" \
    --output-manifest "${BATCH_EXTRACT_MANIFEST}" \
    --max-clips 0 \
    --max-clips-per-study 0 \
    --num-workers "${EXTRACT_NUM_WORKERS}" \
    --progress-every 2000

  # --- 3d: Compute 512-d encoder-only embeddings ---
  log "[batch ${BATCH_IDX}] Computing embeddings..."
  "${PYTHON_BIN}" scripts/extract_echoprime_embeddings.py \
    --extraction-manifest "${BATCH_EXTRACT_MANIFEST}" \
    --weights-dir "${WEIGHTS_DIR}" \
    --output-npz "${BATCH_EMB_DIR}/clip_embeddings_512.npz" \
    --output-manifest "${BATCH_EMB_DIR}/clip_embedding_manifest.csv" \
    --device auto \
    --batch-size "${EMBED_BATCH_SIZE}" \
    --encoder-only \
    --checkpoint-every 5000

  # --- 3e: Purge raw DICOMs and clip NPZs ---
  log "[batch ${BATCH_IDX}] Purging raw data..."
  rm -rf "${BATCH_DOWNLOAD_ROOT}/files"
  rm -rf "${BATCH_NPZ_ROOT}"
  log "[batch ${BATCH_IDX}] Purge complete"

  # Disk check
  AVAIL_GB="$(df --output=avail -BG "${DATA_ROOT}" | tail -1 | tr -d 'G ')"
  log "[batch ${BATCH_IDX}] Storage: ${AVAIL_GB} GB available in projectnb"
done

# ===================================================================
# Step 4: Merge all batch embeddings with existing 500-study results
# ===================================================================
log "=== Step 4: Merging all embeddings ==="

BATCH_EMB_DIRS=()
if [[ -d "${EXISTING_EMB_DIR}" ]] && [[ -f "${EXISTING_EMB_DIR}/clip_embeddings_512.npz" ]]; then
  BATCH_EMB_DIRS+=("${EXISTING_EMB_DIR}")
  log "Including existing 500-study embeddings"
fi
for BATCH_IDX in $(seq 0 $((N_BATCHES - 1))); do
  BATCH_TAG="$(printf 'batch_%03d' "${BATCH_IDX}")"
  BD="${FULLSCALE_ROOT}/batches/${BATCH_TAG}_embeddings"
  if [[ -f "${BD}/clip_embeddings_512.npz" ]]; then
    BATCH_EMB_DIRS+=("${BD}")
  fi
done

MERGED_EMB_DIR="${FULLSCALE_ROOT}/merged_clip_embeddings_512"
mkdir -p "${MERGED_EMB_DIR}"

"${PYTHON_BIN}" scripts/merge_batch_embeddings.py \
  --batch-dirs "${BATCH_EMB_DIRS[@]}" \
  --output-npz "${MERGED_EMB_DIR}/clip_embeddings_512.npz" \
  --output-manifest "${MERGED_EMB_DIR}/clip_embedding_manifest.csv"

# ===================================================================
# Step 5: Aggregate to study-level embeddings
# ===================================================================
log "=== Step 5: Study-level aggregation ==="
STUDY_EMB_DIR="${FULLSCALE_ROOT}/study_embeddings_512"
mkdir -p "${STUDY_EMB_DIR}"

"${PYTHON_BIN}" scripts/aggregate_study_embeddings.py \
  --embedding-npz "${MERGED_EMB_DIR}/clip_embeddings_512.npz" \
  --embedding-manifest "${MERGED_EMB_DIR}/clip_embedding_manifest.csv" \
  --output-npz "${STUDY_EMB_DIR}/study_embeddings_512.npz" \
  --output-manifest "${STUDY_EMB_DIR}/study_embedding_manifest.csv" \
  --method mean

# ===================================================================
# Step 6: Export structured measurements for all studies
# ===================================================================
log "=== Step 6: Exporting structured measurements ==="
"${PYTHON_BIN}" scripts/export_cohort_measurements.py \
  --selected-studies-csv "${ALL_STUDIES_CSV}" \
  --billing-project "${BILLING_PROJECT}" \
  --output-csv "${FULLSCALE_ROOT}/manifests/structured_measurements.csv" \
  --query-sql-out "${FULLSCALE_ROOT}/queries/export_structured_measurements.sql"

# ===================================================================
# Step 7: Build subject split map and LVEF manifest
# ===================================================================
log "=== Step 7: Building split map and LVEF manifest ==="

"${PYTHON_BIN}" scripts/build_subject_split_map.py \
  --selected-studies-csv "${ALL_STUDIES_CSV}" \
  --output-csv "${FULLSCALE_ROOT}/manifests/subject_split_map_v1.csv" \
  --train-frac 0.7 --val-frac 0.15 \
  2>/dev/null || log "[warn] split map builder not found, using inline version"

if [[ ! -f "${FULLSCALE_ROOT}/manifests/subject_split_map_v1.csv" ]]; then
  "${PYTHON_BIN}" - <<'PYEOF'
import numpy as np, pandas as pd, json, sys
from pathlib import Path

csv = Path(sys.argv[1])
out = Path(sys.argv[2])
df = pd.read_csv(csv)
subjects = sorted(df["subject_id"].unique().tolist())
rng = np.random.default_rng(abs(hash("echo-ai-fixed-split-seed-v1")) % (2**31))
rng.shuffle(subjects)
n = len(subjects)
n_train = max(1, int(n * 0.7))
n_val = max(1, int(n * 0.15))
splits = (["train"] * n_train + ["val"] * n_val + ["test"] * (n - n_train - n_val))
split_df = pd.DataFrame({"subject_id": subjects, "split": splits})
out.parent.mkdir(parents=True, exist_ok=True)
split_df.to_csv(out, index=False)
summary = {"n_subjects": n, "split_counts": split_df["split"].value_counts().to_dict()}
out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
PYEOF
  "${ALL_STUDIES_CSV}" "${FULLSCALE_ROOT}/manifests/subject_split_map_v1.csv"
fi

# Build a keyframe manifest stub for LVEF manifest builder
# (we use the merged clip embedding manifest as the keyframe source)
"${PYTHON_BIN}" - <<'PYEOF'
import pandas as pd, sys
from pathlib import Path

emb_manifest = Path(sys.argv[1])
out = Path(sys.argv[2])
df = pd.read_csv(emb_manifest)
stub = df.groupby(["subject_id", "study_id"], as_index=False).first()
stub["write_ok"] = True
stub["keyframe_path"] = stub.get("npz_path", "")
if "dicom_filepath" not in stub.columns:
    stub["dicom_filepath"] = ""
for col in ["selected_index", "n_frames", "method", "focus_score", "motion_score",
            "intensity_mean", "contrast_std"]:
    if col not in stub.columns:
        stub[col] = 0 if col != "method" else "embedding_stub"
out.parent.mkdir(parents=True, exist_ok=True)
stub.to_csv(out, index=False)
print(f"[written] {out} ({len(stub)} rows)")
PYEOF
"${MERGED_EMB_DIR}/clip_embedding_manifest.csv" "${FULLSCALE_ROOT}/manifests/keyframe_stub.csv"

"${PYTHON_BIN}" scripts/build_lvef_still_manifest.py \
  --selected-studies-csv "${ALL_STUDIES_CSV}" \
  --structured-measurements-csv "${FULLSCALE_ROOT}/manifests/structured_measurements.csv" \
  --keyframe-manifest-csv "${FULLSCALE_ROOT}/manifests/keyframe_stub.csv" \
  --output-csv "${FULLSCALE_ROOT}/manifests/lvef_still_manifest.csv" \
  --subject-split-map-csv "${FULLSCALE_ROOT}/manifests/subject_split_map_v1.csv"

# ===================================================================
# Step 8: Run evaluation ladder
# ===================================================================
log "=== Step 8: Running evaluation ladder ==="

log "Running E2b: vision-only study-level baseline..."
"${PYTHON_BIN}" scripts/run_echoprime_embedding_baseline.py \
  --embedding-npz "${STUDY_EMB_DIR}/study_embeddings_512.npz" \
  --embedding-manifest "${STUDY_EMB_DIR}/study_embedding_manifest.csv" \
  --label-manifest "${FULLSCALE_ROOT}/manifests/lvef_still_manifest.csv" \
  --output-dir "${FULLSCALE_ROOT}/eval_e2b_vision" \
  --join-key study_id

log "Running E3: tabular-only baseline..."
"${PYTHON_BIN}" scripts/run_tabular_measurement_baseline.py \
  --measurements-csv "${FULLSCALE_ROOT}/manifests/structured_measurements.csv" \
  --label-manifest "${FULLSCALE_ROOT}/manifests/lvef_still_manifest.csv" \
  --output-dir "${FULLSCALE_ROOT}/eval_e3_tabular"

log "Running E5: multimodal fusion..."
"${PYTHON_BIN}" scripts/run_multimodal_fusion.py \
  --study-embedding-npz "${STUDY_EMB_DIR}/study_embeddings_512.npz" \
  --study-embedding-manifest "${STUDY_EMB_DIR}/study_embedding_manifest.csv" \
  --measurements-csv "${FULLSCALE_ROOT}/manifests/structured_measurements.csv" \
  --label-manifest "${FULLSCALE_ROOT}/manifests/lvef_still_manifest.csv" \
  --output-dir "${FULLSCALE_ROOT}/eval_e5_fusion" \
  --n-bootstrap 2000

log "=== Pipeline complete ==="
log "Results in: ${FULLSCALE_ROOT}"
