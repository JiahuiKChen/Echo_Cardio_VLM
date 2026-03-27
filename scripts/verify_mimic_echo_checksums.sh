#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/verify_mimic_echo_checksums.sh \
    --download-root <root> \
    --sha256-file <SHA256SUMS.txt> \
    --relative-path-list <paths.txt>

This verifies only the selected subset listed in <paths.txt>.
EOF
}

DOWNLOAD_ROOT=""
SHA_FILE=""
RELATIVE_PATH_LIST=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --download-root)
      DOWNLOAD_ROOT="$2"
      shift 2
      ;;
    --sha256-file)
      SHA_FILE="$2"
      shift 2
      ;;
    --relative-path-list)
      RELATIVE_PATH_LIST="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${DOWNLOAD_ROOT}" || -z "${SHA_FILE}" || -z "${RELATIVE_PATH_LIST}" ]]; then
  usage
  exit 1
fi

if [[ ! -d "${DOWNLOAD_ROOT}" ]]; then
  echo "[error] download root not found: ${DOWNLOAD_ROOT}" >&2
  exit 1
fi
if [[ ! -f "${SHA_FILE}" ]]; then
  echo "[error] SHA256SUMS file not found: ${SHA_FILE}" >&2
  exit 1
fi
if [[ ! -f "${RELATIVE_PATH_LIST}" ]]; then
  echo "[error] relative path list not found: ${RELATIVE_PATH_LIST}" >&2
  exit 1
fi

TMP_MANIFEST="$(mktemp)"
trap 'rm -f "${TMP_MANIFEST}"' EXIT

python3 - <<'PY' "${SHA_FILE}" "${RELATIVE_PATH_LIST}" "${TMP_MANIFEST}"
import sys
from pathlib import Path

sha_file = Path(sys.argv[1])
paths_file = Path(sys.argv[2])
out_file = Path(sys.argv[3])

selected = [line.strip() for line in paths_file.read_text().splitlines() if line.strip()]
selected_set = set(selected)
matched = []

for line in sha_file.read_text().splitlines():
    if not line.strip():
        continue
    parts = line.split()
    rel_path = parts[-1].lstrip("./")
    if rel_path in selected_set:
        matched.append(line)

missing = sorted(selected_set - {line.split()[-1].lstrip("./") for line in matched})
if missing:
    print("[error] The following files were not found in SHA256SUMS:", file=sys.stderr)
    for item in missing:
        print(item, file=sys.stderr)
    raise SystemExit(1)

out_file.write_text("".join(f"{line}\n" for line in matched))
print(f"[info] Wrote {len(matched)} checksum lines to {out_file}")
PY

(
  cd "${DOWNLOAD_ROOT}"
  shasum -a 256 -c "${TMP_MANIFEST}"
)

echo "[done] Checksum verification completed"
