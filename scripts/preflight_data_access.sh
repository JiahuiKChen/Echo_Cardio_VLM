#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Preflight checks for MIMIC ECHO data + cloud access.

Checks:
1) CLI tooling availability (`bq`, `gcloud`, `gsutil`)
2) BigQuery dataset/table accessibility
3) Echo note linkage probe (EC note_id join to mimiciv_note.radiology)
4) GCS bucket metadata + object availability (1.0 vs 0.1)
5) Recommended active bucket selection

Usage:
  ./scripts/preflight_data_access.sh \
    --billing-project <gcp_project_for_requester_pays_and_bq_jobs> \
    [--bq-data-project physionet-data] \
    [--echo-dataset mimiciv_echo] \
    [--note-dataset mimiciv_note] \
    [--hosp-dataset mimiciv_3_1_hosp] \
    [--bucket-primary mimic-iv-echo-1.0.physionet.org] \
    [--bucket-fallback mimic-iv-echo-0.1.physionet.org] \
    [--output-dir outputs/access_preflight]
EOF
}

BILLING_PROJECT=""
BQ_DATA_PROJECT="physionet-data"
ECHO_DATASET="mimiciv_echo"
NOTE_DATASET="mimiciv_note"
HOSP_DATASET="mimiciv_3_1_hosp"
BUCKET_PRIMARY="mimic-iv-echo-1.0.physionet.org"
BUCKET_FALLBACK="mimic-iv-echo-0.1.physionet.org"
OUTPUT_DIR="${REPO_ROOT}/outputs/access_preflight"
MAX_LIST_LINES=20

while [[ $# -gt 0 ]]; do
  case "$1" in
    --billing-project) BILLING_PROJECT="$2"; shift 2 ;;
    --bq-data-project) BQ_DATA_PROJECT="$2"; shift 2 ;;
    --echo-dataset) ECHO_DATASET="$2"; shift 2 ;;
    --note-dataset) NOTE_DATASET="$2"; shift 2 ;;
    --hosp-dataset) HOSP_DATASET="$2"; shift 2 ;;
    --bucket-primary) BUCKET_PRIMARY="$2"; shift 2 ;;
    --bucket-fallback) BUCKET_FALLBACK="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --max-list-lines) MAX_LIST_LINES="$2"; shift 2 ;;
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

for cmd in bq gcloud gsutil python3; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "[error] Required command not found: ${cmd}" >&2
    exit 1
  fi
done

mkdir -p "${OUTPUT_DIR}"
STAMP="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="${OUTPUT_DIR}/${STAMP}"
mkdir -p "${RUN_DIR}"

status_csv="${RUN_DIR}/checks.csv"
echo "check,status,detail" > "${status_csv}"

record_check() {
  local check_name="$1"
  local status="$2"
  local detail="$3"
  detail="${detail//$'\n'/ }"
  detail="${detail//,/;}"
  echo "${check_name},${status},${detail}" >> "${status_csv}"
}

run_cmd_capture() {
  local outfile="$1"
  shift
  if "$@" >"${outfile}" 2>&1; then
    return 0
  fi
  return 1
}

echo "[info] Output dir: ${RUN_DIR}"

echo "[info] Checking BigQuery dataset visibility..."
if run_cmd_capture "${RUN_DIR}/bq_ls_echo.txt" bq --project_id="${BILLING_PROJECT}" ls "${BQ_DATA_PROJECT}:${ECHO_DATASET}"; then
  record_check "bq_ls_echo" "ok" "${BQ_DATA_PROJECT}:${ECHO_DATASET} visible"
else
  record_check "bq_ls_echo" "fail" "$(cat "${RUN_DIR}/bq_ls_echo.txt")"
fi

if run_cmd_capture "${RUN_DIR}/bq_ls_note.txt" bq --project_id="${BILLING_PROJECT}" ls "${BQ_DATA_PROJECT}:${NOTE_DATASET}"; then
  record_check "bq_ls_note" "ok" "${BQ_DATA_PROJECT}:${NOTE_DATASET} visible"
else
  record_check "bq_ls_note" "fail" "$(cat "${RUN_DIR}/bq_ls_note.txt")"
fi

if run_cmd_capture "${RUN_DIR}/bq_ls_hosp.txt" bq --project_id="${BILLING_PROJECT}" ls "${BQ_DATA_PROJECT}:${HOSP_DATASET}"; then
  record_check "bq_ls_hosp" "ok" "${BQ_DATA_PROJECT}:${HOSP_DATASET} visible"
else
  record_check "bq_ls_hosp" "fail" "$(cat "${RUN_DIR}/bq_ls_hosp.txt")"
fi

echo "[info] Running echo/note linkage probes..."
if run_cmd_capture "${RUN_DIR}/probe_echo_counts.json" \
  bq --project_id="${BILLING_PROJECT}" query --nouse_legacy_sql --format=prettyjson \
  "SELECT
      COUNT(*) AS n_echo_studies,
      COUNTIF(note_id IS NOT NULL) AS n_echo_nonnull_note_id
   FROM \`${BQ_DATA_PROJECT}.${ECHO_DATASET}.echo_study_list\`"; then
  record_check "probe_echo_counts" "ok" "echo study counts queried"
else
  record_check "probe_echo_counts" "fail" "$(cat "${RUN_DIR}/probe_echo_counts.json")"
fi

if run_cmd_capture "${RUN_DIR}/probe_note_join.json" \
  bq --project_id="${BILLING_PROJECT}" query --nouse_legacy_sql --format=prettyjson \
  "WITH e AS (
      SELECT subject_id, note_id
      FROM \`${BQ_DATA_PROJECT}.${ECHO_DATASET}.echo_study_list\`
      WHERE note_id IS NOT NULL
    )
    SELECT
      COUNT(*) AS n_echo_with_note_id,
      COUNT(r.note_id) AS n_joined_radiology
    FROM e
    LEFT JOIN \`${BQ_DATA_PROJECT}.${NOTE_DATASET}.radiology\` r
      ON e.subject_id = r.subject_id
     AND e.note_id = r.note_id"; then
  record_check "probe_note_join" "ok" "echo->radiology note_id join tested"
else
  record_check "probe_note_join" "fail" "$(cat "${RUN_DIR}/probe_note_join.json")"
fi

echo "[info] Checking bucket metadata + object visibility..."
if run_cmd_capture "${RUN_DIR}/bucket_primary_describe.txt" \
  gcloud storage buckets describe "gs://${BUCKET_PRIMARY}" --billing-project="${BILLING_PROJECT}"; then
  record_check "bucket_primary_describe" "ok" "${BUCKET_PRIMARY} describable"
else
  record_check "bucket_primary_describe" "fail" "$(cat "${RUN_DIR}/bucket_primary_describe.txt")"
fi

if run_cmd_capture "${RUN_DIR}/bucket_fallback_describe.txt" \
  gcloud storage buckets describe "gs://${BUCKET_FALLBACK}" --billing-project="${BILLING_PROJECT}"; then
  record_check "bucket_fallback_describe" "ok" "${BUCKET_FALLBACK} describable"
else
  record_check "bucket_fallback_describe" "fail" "$(cat "${RUN_DIR}/bucket_fallback_describe.txt")"
fi

primary_du_file="${RUN_DIR}/bucket_primary_du.txt"
fallback_du_file="${RUN_DIR}/bucket_fallback_du.txt"
primary_ls_file="${RUN_DIR}/bucket_primary_ls_head.txt"
fallback_ls_file="${RUN_DIR}/bucket_fallback_ls_head.txt"

if run_cmd_capture "${primary_du_file}" gsutil -u "${BILLING_PROJECT}" du -s "gs://${BUCKET_PRIMARY}"; then
  record_check "bucket_primary_du" "ok" "$(cat "${primary_du_file}")"
else
  record_check "bucket_primary_du" "fail" "$(cat "${primary_du_file}")"
fi

if run_cmd_capture "${fallback_du_file}" gsutil -u "${BILLING_PROJECT}" du -s "gs://${BUCKET_FALLBACK}"; then
  record_check "bucket_fallback_du" "ok" "$(cat "${fallback_du_file}")"
else
  record_check "bucket_fallback_du" "fail" "$(cat "${fallback_du_file}")"
fi

if run_cmd_capture "${primary_ls_file}" bash -lc "gsutil -u '${BILLING_PROJECT}' ls 'gs://${BUCKET_PRIMARY}' | head -n ${MAX_LIST_LINES}"; then
  if [[ -s "${primary_ls_file}" ]]; then
    record_check "bucket_primary_ls" "ok" "listed ${MAX_LIST_LINES} lines (or fewer)"
  else
    record_check "bucket_primary_ls" "warn" "no objects listed"
  fi
else
  record_check "bucket_primary_ls" "fail" "$(cat "${primary_ls_file}")"
fi

if run_cmd_capture "${fallback_ls_file}" bash -lc "gsutil -u '${BILLING_PROJECT}' ls 'gs://${BUCKET_FALLBACK}' | head -n ${MAX_LIST_LINES}"; then
  if [[ -s "${fallback_ls_file}" ]]; then
    record_check "bucket_fallback_ls" "ok" "listed ${MAX_LIST_LINES} lines (or fewer)"
  else
    record_check "bucket_fallback_ls" "warn" "no objects listed"
  fi
else
  record_check "bucket_fallback_ls" "fail" "$(cat "${fallback_ls_file}")"
fi

echo "[info] Building summary..."
python3 - <<PY
import json
from pathlib import Path
import re
import pandas as pd

run_dir = Path("${RUN_DIR}")
status_csv = run_dir / "checks.csv"
checks = pd.read_csv(status_csv)

def parse_du_bytes(path: Path):
    if not path.exists():
        return None
    txt = path.read_text().strip()
    m = re.match(r"^\s*(\d+)\s+gs://", txt)
    if not m:
        return None
    return int(m.group(1))

def parse_probe_value(path: Path, key: str):
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text())
        if isinstance(obj, list) and obj:
            return int(obj[0].get(key))
    except Exception:
        return None
    return None

primary_bytes = parse_du_bytes(run_dir / "bucket_primary_du.txt")
fallback_bytes = parse_du_bytes(run_dir / "bucket_fallback_du.txt")
n_echo_nonnull_note_id = parse_probe_value(run_dir / "probe_echo_counts.json", "n_echo_nonnull_note_id")
n_note_joined = parse_probe_value(run_dir / "probe_note_join.json", "n_joined_radiology")

recommended_bucket = None
bucket_reason = None
if primary_bytes and primary_bytes > 0:
    recommended_bucket = "${BUCKET_PRIMARY}"
    bucket_reason = "primary bucket has non-zero object bytes"
elif fallback_bytes and fallback_bytes > 0:
    recommended_bucket = "${BUCKET_FALLBACK}"
    bucket_reason = "primary appears empty; fallback has non-zero object bytes"
else:
    bucket_reason = "both buckets appear empty or inaccessible from current credentials"

summary = {
    "timestamp": "${STAMP}",
    "billing_project": "${BILLING_PROJECT}",
    "bq_data_project": "${BQ_DATA_PROJECT}",
    "datasets": {
        "echo": "${ECHO_DATASET}",
        "note": "${NOTE_DATASET}",
        "hosp": "${HOSP_DATASET}",
    },
    "gcs_buckets": {
        "primary": "${BUCKET_PRIMARY}",
        "fallback": "${BUCKET_FALLBACK}",
        "primary_bytes": primary_bytes,
        "fallback_bytes": fallback_bytes,
        "recommended_active_bucket": recommended_bucket,
        "recommendation_reason": bucket_reason,
    },
    "note_linkage_probe": {
        "n_echo_nonnull_note_id": n_echo_nonnull_note_id,
        "n_joined_radiology": n_note_joined,
        "echo_note_id_join_status": "ok" if (n_note_joined or 0) > 0 else "unresolved",
    },
    "checks": checks.to_dict(orient="records"),
}

(run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
print(f"[written] {run_dir / 'summary.json'}")
PY

ln -sfn "${RUN_DIR}" "${OUTPUT_DIR}/latest"

echo "[done] Preflight complete."
echo "[done] Run dir: ${RUN_DIR}"
echo "[done] Latest symlink: ${OUTPUT_DIR}/latest"
