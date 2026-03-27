#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Check live status for Stage D full multiframe extraction.

Usage:
  ./scripts/check_stage_d_full_status.sh \
    [--output-root '/Volumes/MIMIC ECHO Drive/echo_ai/cloud_cohorts/stage_d_500study/derived/full_npz_224x32'] \
    [--total 21393]
EOF
}

OUTPUT_ROOT="/Volumes/MIMIC ECHO Drive/echo_ai/cloud_cohorts/stage_d_500study/derived/full_npz_224x32"
TOTAL=21393

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --total) TOTAL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "[error] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

COUNT="$(find "${OUTPUT_ROOT}" -type f -name '*.npz' 2>/dev/null | wc -l | tr -d ' ')"
PID="$(pgrep -f 'extract_mimic_echo_cines.py --audit-csv outputs/cloud_cohorts/stage_d_500study/audit/cine_candidates.csv' | head -n 1 || true)"

echo "[status] output_root=${OUTPUT_ROOT}"
echo "[status] npz_count=${COUNT}"
echo "[status] target_total=${TOTAL}"

if [[ -z "${PID}" ]]; then
  echo "[status] extractor_running=false"
  exit 0
fi

ETIME="$(ps -o etime= -p "${PID}" | tr -d ' ')"
echo "[status] extractor_running=true"
echo "[status] pid=${PID}"
echo "[status] elapsed=${ETIME}"

python3 - <<PY
import math

count = int("${COUNT}")
total = int("${TOTAL}")
etime = "${ETIME}"

def parse_etime(v: str) -> int:
    days = 0
    if "-" in v:
        d, v = v.split("-", 1)
        days = int(d)
    parts = [int(x) for x in v.split(":")]
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = 0, parts[0], parts[1]
    else:
        h, m, s = 0, 0, parts[0]
    return (((days * 24) + h) * 60 + m) * 60 + s

elapsed_s = parse_etime(etime)
if elapsed_s <= 0 or count <= 0:
    print("[status] rate_npz_per_min=unknown")
    print("[status] eta=unknown")
    raise SystemExit(0)

rate_per_min = count / (elapsed_s / 60)
remaining = max(total - count, 0)
eta_min = remaining / rate_per_min if rate_per_min > 0 else math.inf

eta_h = int(eta_min // 60)
eta_m = int(round(eta_min % 60))
print(f"[status] rate_npz_per_min={rate_per_min:.1f}")
print(f"[status] remaining={remaining}")
print(f"[status] eta≈{eta_h}h{eta_m:02d}m")
PY
