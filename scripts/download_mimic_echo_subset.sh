#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/download_mimic_echo_subset.sh --url-list <urls.txt> --dest <download_root>

Auth modes:
  1. Export PHYSIONET_USER and enter the password interactively.
  2. Export PHYSIONET_COOKIES with a Netscape-format cookie file path.

Examples:
  export PHYSIONET_USER='your_physionet_username'
  ./scripts/download_mimic_echo_subset.sh \
    --url-list outputs/mimic_echo_subset/stage_b_pilot/download_urls.txt \
    --dest '/Volumes/MIMIC ECHO Drive/echo_ai/mimic-iv-echo'

  export PHYSIONET_COOKIES="$HOME/Downloads/physionet_cookies.txt"
  ./scripts/download_mimic_echo_subset.sh \
    --url-list outputs/mimic_echo_subset/stage_b_pilot/download_urls.txt \
    --dest '/Volumes/MIMIC ECHO Drive/echo_ai/mimic-iv-echo'
EOF
}

URL_LIST=""
DEST=""
CUT_DIRS="${CUT_DIRS:-3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url-list)
      URL_LIST="$2"
      shift 2
      ;;
    --dest)
      DEST="$2"
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

if [[ -z "${URL_LIST}" || -z "${DEST}" ]]; then
  usage
  exit 1
fi

if [[ ! -f "${URL_LIST}" ]]; then
  echo "[error] URL list not found: ${URL_LIST}" >&2
  exit 1
fi

AUTH_ARGS=()
if [[ -n "${PHYSIONET_COOKIES:-}" ]]; then
  if [[ ! -f "${PHYSIONET_COOKIES}" ]]; then
    echo "[error] PHYSIONET_COOKIES does not exist: ${PHYSIONET_COOKIES}" >&2
    exit 1
  fi
  AUTH_ARGS+=(--load-cookies "${PHYSIONET_COOKIES}")
elif [[ -n "${PHYSIONET_USER:-}" ]]; then
  AUTH_ARGS+=(--user "${PHYSIONET_USER}" --ask-password)
else
  echo "[error] Set PHYSIONET_USER or PHYSIONET_COOKIES before running this script." >&2
  exit 1
fi

mkdir -p "${DEST}"

echo "[info] Download root: ${DEST}"
echo "[info] URL list: ${URL_LIST}"
echo "[info] URL count: $(wc -l < "${URL_LIST}")"

wget \
  --continue \
  --timestamping \
  --force-directories \
  --no-host-directories \
  --cut-dirs="${CUT_DIRS}" \
  --directory-prefix="${DEST}" \
  --retry-connrefused \
  --waitretry=5 \
  --read-timeout=30 \
  --timeout=30 \
  --tries=20 \
  --input-file="${URL_LIST}" \
  "${AUTH_ARGS[@]}"

echo "[done] Download completed"
