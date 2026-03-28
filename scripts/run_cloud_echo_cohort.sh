#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Run a cloud-backed MIMIC-IV-ECHO cohort pipeline:
1) select studies from BigQuery metadata
2) pull selected studies from requester-pays GCS bucket
3) run DICOM audit
4) run capped cine extraction smoke test

Usage:
  ./scripts/run_cloud_echo_cohort.sh \
    --billing-project <gcp_project_for_requester_pays> \
    --cohort-root <output_dir> \
    --download-root <local_data_root>

Required:
  --billing-project   GCP project used for BigQuery query jobs and requester-pays billing

Optional:
  --cohort-root                  Default: outputs/cloud_cohorts/stage_c_50study
  --download-root                Default: $ECHO_AI_DATA_ROOT/cloud_cohorts/stage_c_50study
  --strict-download-root         Default: true (fail if external mount/path is unavailable)
  --fallback-download-root       Default: <cohort-root>_data (used only when strict=false)
  --min-free-gb                  Default: 200 (warn/fail threshold for download destination)
  --n-studies                    Default: 50
  --seed                         Default: 1337
  --min-dicoms                   Default: 40
  --max-dicoms                   Default: 120
  --max-studies-per-subject      Default: 1 (set 0 to disable)
  --require-note-link            Default: true
  --require-measurement-link     Default: true
  --bq-project                   Default: physionet-data
  --bq-dataset                   Default: mimiciv_echo
  --gcs-bucket                   Default: mimic-iv-echo-1.0.physionet.org
  --python-bin                   Default: .venv-echoprime/bin/python
  --run-audit                    Default: true
  --run-extract-smoke            Default: true
  --extract-max-clips            Default: 100
  --extract-max-clips-per-study  Default: 2
  --extract-num-workers          Default: 1
  --extract-overwrite-existing   Default: false
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

volume_mount_for_path() {
  local path="$1"
  local suffix
  suffix="${path#/Volumes/}"
  if [[ "${suffix}" == "${path}" ]]; then
    echo ""
    return 0
  fi
  local vol
  vol="${suffix%%/*}"
  echo "/Volumes/${vol}"
}

ensure_min_free_gb() {
  local path="$1"
  local min_free_gb="$2"
  if [[ "${min_free_gb}" -le 0 ]]; then
    return 0
  fi
  local avail_kb
  avail_kb="$(df -Pk "${path}" | awk 'NR==2 {print $4}')"
  if [[ -z "${avail_kb}" ]]; then
    echo "[warn] Could not determine free space for ${path}; continuing." >&2
    return 0
  fi
  local avail_gb
  avail_gb=$((avail_kb / 1024 / 1024))
  if [[ "${avail_gb}" -lt "${min_free_gb}" ]]; then
    echo "[error] Insufficient free space at ${path}: ${avail_gb} GB available, need at least ${min_free_gb} GB." >&2
    exit 1
  fi
  echo "[info] Free space check passed at ${path}: ${avail_gb} GB available (threshold ${min_free_gb} GB)."
}

resolve_download_root() {
  local requested="$1"
  local strict="$2"
  local fallback="$3"

  local mount_point
  mount_point="$(volume_mount_for_path "${requested}")"
  if [[ -n "${mount_point}" && ! -d "${mount_point}" ]]; then
    if [[ "${strict}" == "true" ]]; then
      echo "[error] External volume appears unmounted: ${mount_point}" >&2
      echo "[error] Refusing to proceed because --strict-download-root=true." >&2
      exit 1
    fi
    echo "[warn] External volume appears unmounted: ${mount_point}" >&2
    echo "[warn] Falling back to local path: ${fallback}" >&2
    requested="${fallback}"
  fi

  if ! mkdir -p "${requested}" 2>/dev/null; then
    if [[ "${strict}" == "true" ]]; then
      echo "[error] Could not create download root: ${requested}" >&2
      echo "[error] Refusing to proceed because --strict-download-root=true." >&2
      exit 1
    fi
    echo "[warn] Could not create download root: ${requested}" >&2
    echo "[warn] Falling back to local path: ${fallback}" >&2
    requested="${fallback}"
    mkdir -p "${requested}"
  fi

  echo "${requested}"
}

BILLING_PROJECT=""
COHORT_ROOT="${REPO_ROOT}/outputs/cloud_cohorts/stage_c_50study"
ECHO_AI_DATA_ROOT="${ECHO_AI_DATA_ROOT:-${REPO_ROOT}/outputs/data}"
DOWNLOAD_ROOT="${ECHO_AI_DATA_ROOT}/cloud_cohorts/stage_c_50study"
STRICT_DOWNLOAD_ROOT="true"
FALLBACK_DOWNLOAD_ROOT=""
MIN_FREE_GB=200
N_STUDIES=50
SEED=1337
MIN_DICOMS=40
MAX_DICOMS=120
MAX_STUDIES_PER_SUBJECT=1
REQUIRE_NOTE_LINK="true"
REQUIRE_MEASUREMENT_LINK="true"
BQ_PROJECT="physionet-data"
BQ_DATASET="mimiciv_echo"
GCS_BUCKET="mimic-iv-echo-1.0.physionet.org"
DEFAULT_PYTHON_BIN="${REPO_ROOT}/.venv-echoprime/bin/python"
if [[ ! -x "${DEFAULT_PYTHON_BIN}" ]]; then
  DEFAULT_PYTHON_BIN="python3"
fi
PYTHON_BIN="${DEFAULT_PYTHON_BIN}"
RUN_AUDIT="true"
RUN_EXTRACT_SMOKE="true"
EXTRACT_MAX_CLIPS=100
EXTRACT_MAX_CLIPS_PER_STUDY=2
EXTRACT_NUM_WORKERS=1
EXTRACT_OVERWRITE_EXISTING="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --billing-project) BILLING_PROJECT="$2"; shift 2 ;;
    --cohort-root) COHORT_ROOT="$2"; shift 2 ;;
    --download-root) DOWNLOAD_ROOT="$2"; shift 2 ;;
    --strict-download-root) STRICT_DOWNLOAD_ROOT="$(to_bool "$2")"; shift 2 ;;
    --fallback-download-root) FALLBACK_DOWNLOAD_ROOT="$2"; shift 2 ;;
    --min-free-gb) MIN_FREE_GB="$2"; shift 2 ;;
    --n-studies) N_STUDIES="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --min-dicoms) MIN_DICOMS="$2"; shift 2 ;;
    --max-dicoms) MAX_DICOMS="$2"; shift 2 ;;
    --max-studies-per-subject) MAX_STUDIES_PER_SUBJECT="$2"; shift 2 ;;
    --require-note-link) REQUIRE_NOTE_LINK="$(to_bool "$2")"; shift 2 ;;
    --require-measurement-link) REQUIRE_MEASUREMENT_LINK="$(to_bool "$2")"; shift 2 ;;
    --bq-project) BQ_PROJECT="$2"; shift 2 ;;
    --bq-dataset) BQ_DATASET="$2"; shift 2 ;;
    --gcs-bucket) GCS_BUCKET="$2"; shift 2 ;;
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --run-audit) RUN_AUDIT="$(to_bool "$2")"; shift 2 ;;
    --run-extract-smoke) RUN_EXTRACT_SMOKE="$(to_bool "$2")"; shift 2 ;;
    --extract-max-clips) EXTRACT_MAX_CLIPS="$2"; shift 2 ;;
    --extract-max-clips-per-study) EXTRACT_MAX_CLIPS_PER_STUDY="$2"; shift 2 ;;
    --extract-num-workers) EXTRACT_NUM_WORKERS="$2"; shift 2 ;;
    --extract-overwrite-existing) EXTRACT_OVERWRITE_EXISTING="$(to_bool "$2")"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${BILLING_PROJECT}" ]]; then
  echo "[error] --billing-project is required." >&2
  usage
  exit 1
fi

for cmd in bq gcloud; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "[error] Required command not found: ${cmd}" >&2
    exit 1
  fi
done

if [[ "${PYTHON_BIN}" == */* ]]; then
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "[error] Python executable not found or not executable: ${PYTHON_BIN}" >&2
    exit 1
  fi
else
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "[error] Python command not found in PATH: ${PYTHON_BIN}" >&2
    exit 1
  fi
fi

mkdir -p "$(dirname "${COHORT_ROOT}")"
COHORT_ROOT="$(cd "$(dirname "${COHORT_ROOT}")" && pwd)/$(basename "${COHORT_ROOT}")"
mkdir -p "${COHORT_ROOT}"

if [[ -z "${FALLBACK_DOWNLOAD_ROOT}" ]]; then
  FALLBACK_DOWNLOAD_ROOT="${COHORT_ROOT}_data"
fi

DOWNLOAD_ROOT="$(resolve_download_root "${DOWNLOAD_ROOT}" "${STRICT_DOWNLOAD_ROOT}" "${FALLBACK_DOWNLOAD_ROOT}")"
ensure_min_free_gb "${DOWNLOAD_ROOT}" "${MIN_FREE_GB}"

QUERY_DIR="${COHORT_ROOT}/queries"
MANIFEST_DIR="${COHORT_ROOT}/manifests"
LOG_DIR="${COHORT_ROOT}/logs"
mkdir -p "${QUERY_DIR}" "${MANIFEST_DIR}" "${LOG_DIR}"

SELECTED_STUDIES_CSV="${MANIFEST_DIR}/selected_studies.csv"
SELECTED_RECORDS_CSV="${MANIFEST_DIR}/selected_records.csv"
DOWNLOAD_REPORT_CSV="${MANIFEST_DIR}/download_report.csv"
SELECTION_SUMMARY_JSON="${MANIFEST_DIR}/selection_summary.json"

MEASUREMENT_EXPR="TRUE"
NOTE_EXPR="TRUE"
SUBJECT_CAP_EXPR="TRUE"

if [[ "${REQUIRE_MEASUREMENT_LINK}" == "true" ]]; then
  MEASUREMENT_EXPR="measurement_id IS NOT NULL"
fi
if [[ "${REQUIRE_NOTE_LINK}" == "true" ]]; then
  NOTE_EXPR="note_id IS NOT NULL"
fi
if [[ "${MAX_STUDIES_PER_SUBJECT}" -gt 0 ]]; then
  SUBJECT_CAP_EXPR="rn_subject <= ${MAX_STUDIES_PER_SUBJECT}"
fi

STUDIES_SQL_FILE="${QUERY_DIR}/select_studies.sql"
cat > "${STUDIES_SQL_FILE}" <<EOF
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
    AND (${MEASUREMENT_EXPR})
    AND (${NOTE_EXPR})
),
stats AS (
  SELECT APPROX_QUANTILES(n_dicoms, 100)[OFFSET(50)] AS median_dicoms
  FROM filtered
),
ranked AS (
  SELECT
    f.*,
    ABS(f.n_dicoms - (SELECT median_dicoms FROM stats)) AS score_abs_dicoms,
    ROW_NUMBER() OVER (
      PARTITION BY f.subject_id
      ORDER BY
        ABS(f.n_dicoms - (SELECT median_dicoms FROM stats)),
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
WHERE ${SUBJECT_CAP_EXPR}
ORDER BY
  score_abs_dicoms,
  FARM_FINGERPRINT(CONCAT(CAST(study_id AS STRING), '-', CAST(${SEED} AS STRING)))
LIMIT ${N_STUDIES}
EOF

RECORDS_SQL_FILE="${QUERY_DIR}/select_records.sql"
cat > "${RECORDS_SQL_FILE}" <<EOF
WITH selected_studies AS (
  $(cat "${STUDIES_SQL_FILE}")
)
SELECT
  r.subject_id,
  r.study_id,
  r.acquisition_datetime,
  r.dicom_filepath,
  REPLACE(r.dicom_filepath, 'files/', '') AS gcs_object_path
FROM \`${BQ_PROJECT}.${BQ_DATASET}.echo_record_list\` r
JOIN selected_studies s
  USING (subject_id, study_id)
ORDER BY r.study_id, r.dicom_filepath
EOF

echo "[info] Selecting studies from BigQuery..."
bq --project_id="${BILLING_PROJECT}" query \
  --nouse_legacy_sql \
  --format=csv \
  --max_rows=1000000 \
  "$(cat "${STUDIES_SQL_FILE}")" > "${SELECTED_STUDIES_CSV}"

if [[ ! -s "${SELECTED_STUDIES_CSV}" ]]; then
  echo "[error] selected_studies.csv is empty: ${SELECTED_STUDIES_CSV}" >&2
  exit 1
fi

STUDY_ROWS="$(tail -n +2 "${SELECTED_STUDIES_CSV}" | wc -l | tr -d ' ')"
if [[ "${STUDY_ROWS}" -lt "${N_STUDIES}" ]]; then
  echo "[error] Requested ${N_STUDIES} studies but only ${STUDY_ROWS} were selected." >&2
  exit 1
fi
echo "[info] Selected studies: ${STUDY_ROWS}"

echo "[info] Selecting DICOM rows for chosen studies..."
bq --project_id="${BILLING_PROJECT}" query \
  --nouse_legacy_sql \
  --format=csv \
  --max_rows=10000000 \
  "$(cat "${RECORDS_SQL_FILE}")" > "${SELECTED_RECORDS_CSV}"

if [[ ! -s "${SELECTED_RECORDS_CSV}" ]]; then
  echo "[error] selected_records.csv is empty: ${SELECTED_RECORDS_CSV}" >&2
  exit 1
fi

RECORD_ROWS="$(tail -n +2 "${SELECTED_RECORDS_CSV}" | wc -l | tr -d ' ')"
echo "[info] Selected DICOM rows: ${RECORD_ROWS}"

echo "subject_id,study_id,expected_dicoms,actual_dicoms,status" > "${DOWNLOAD_REPORT_CSV}"

echo "[info] Downloading selected studies from gs://${GCS_BUCKET} ..."
tail -n +2 "${SELECTED_STUDIES_CSV}" | while IFS=, read -r subject_id study_id n_dicoms _; do
  pfx="$(printf 'p%02d' "$((subject_id / 1000000))")"
  parent_dir="${DOWNLOAD_ROOT}/files/${pfx}/p${subject_id}"
  study_dir="${parent_dir}/s${study_id}"
  mkdir -p "${parent_dir}"

  existing=0
  if [[ -d "${study_dir}" ]]; then
    existing="$(find "${study_dir}" -maxdepth 1 -type f -name '*.dcm' 2>/dev/null | wc -l | tr -d ' ')"
  fi

  if [[ "${existing}" -ge "${n_dicoms}" ]]; then
    echo "[skip] subject=${subject_id} study=${study_id} already complete (${existing}/${n_dicoms})"
    echo "${subject_id},${study_id},${n_dicoms},${existing},already_complete" >> "${DOWNLOAD_REPORT_CSV}"
    continue
  fi

  src_uri="gs://${GCS_BUCKET}/files/${pfx}/p${subject_id}/s${study_id}"
  echo "[copy] ${src_uri} -> ${study_dir}"
  gcloud storage cp \
    --recursive \
    --billing-project="${BILLING_PROJECT}" \
    "${src_uri}" \
    "${parent_dir}"

  actual="$(find "${study_dir}" -maxdepth 1 -type f -name '*.dcm' 2>/dev/null | wc -l | tr -d ' ')"
  status="ok"
  if [[ "${actual}" -lt "${n_dicoms}" ]]; then
    status="incomplete"
  fi
  echo "${subject_id},${study_id},${n_dicoms},${actual},${status}" >> "${DOWNLOAD_REPORT_CSV}"
done

echo "[info] Building selection summary..."
"${PYTHON_BIN}" - <<PY
import json
from pathlib import Path
import pandas as pd

studies = pd.read_csv("${SELECTED_STUDIES_CSV}")
records = pd.read_csv("${SELECTED_RECORDS_CSV}")
download = pd.read_csv("${DOWNLOAD_REPORT_CSV}")
summary = {
    "billing_project": "${BILLING_PROJECT}",
    "bq_dataset": "${BQ_PROJECT}.${BQ_DATASET}",
    "gcs_bucket": "${GCS_BUCKET}",
    "n_selected_studies": int(studies["study_id"].nunique()),
    "n_selected_subjects": int(studies["subject_id"].nunique()),
    "n_selected_dicoms": int(len(records)),
    "selected_dicoms_mean_per_study": float(studies["n_dicoms"].mean()),
    "selected_dicoms_median_per_study": float(studies["n_dicoms"].median()),
    "n_download_complete_studies": int((download["status"] == "ok").sum() + (download["status"] == "already_complete").sum()),
    "n_download_incomplete_studies": int((download["status"] == "incomplete").sum()),
    "download_root": "${DOWNLOAD_ROOT}",
}
Path("${SELECTION_SUMMARY_JSON}").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
PY

if [[ "${RUN_AUDIT}" == "true" ]]; then
  echo "[info] Running DICOM audit..."
  AUDIT_OUT="${COHORT_ROOT}/audit"
  mkdir -p "${AUDIT_OUT}"
  "${PYTHON_BIN}" "${SCRIPT_DIR}/audit_mimic_echo_dicoms.py" \
    --records-csv "${SELECTED_RECORDS_CSV}" \
    --data-root "${DOWNLOAD_ROOT}" \
    --output-dir "${AUDIT_OUT}"
fi

if [[ "${RUN_EXTRACT_SMOKE}" == "true" ]]; then
  if [[ "${RUN_AUDIT}" != "true" ]]; then
    echo "[error] --run-extract-smoke=true requires --run-audit=true." >&2
    exit 1
  fi
  echo "[info] Running cine extraction smoke test..."
  EXTRACT_OUT="${COHORT_ROOT}/extract_smoke"
  EXTRACT_ROOT="${DOWNLOAD_ROOT}/derived/smoke_npz"
  mkdir -p "${EXTRACT_OUT}" "${EXTRACT_ROOT}"
  EXTRACT_CMD=(
    "${PYTHON_BIN}" "${SCRIPT_DIR}/extract_mimic_echo_cines.py"
    --audit-csv "${COHORT_ROOT}/audit/cine_candidates.csv" \
    --data-root "${DOWNLOAD_ROOT}" \
    --output-root "${EXTRACT_ROOT}" \
    --output-manifest "${EXTRACT_OUT}/extraction_manifest.csv" \
    --max-clips "${EXTRACT_MAX_CLIPS}" \
    --max-clips-per-study "${EXTRACT_MAX_CLIPS_PER_STUDY}" \
    --num-workers "${EXTRACT_NUM_WORKERS}" \
    --target-size 224 \
    --target-frames 32
  )
  if [[ "${EXTRACT_OVERWRITE_EXISTING}" == "true" ]]; then
    EXTRACT_CMD+=(--overwrite-existing)
  fi
  "${EXTRACT_CMD[@]}"
fi

echo "[done] Cloud cohort pipeline completed."
echo "[done] Cohort root: ${COHORT_ROOT}"
echo "[done] Download root: ${DOWNLOAD_ROOT}"
