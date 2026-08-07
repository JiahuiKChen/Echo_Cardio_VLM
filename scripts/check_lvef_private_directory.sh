#!/usr/bin/env bash
# Accept an owner-private directory on ordinary or SCC setgid filesystems.

set -euo pipefail

if [[ "$#" -ne 1 || -z "$1" || "$1" != /* ]]; then
  printf '%s\n' 'lvef_private_directory_status=FAIL_INVALID_ARGUMENT' >&2
  exit 64
fi

LVEF_PRIVATE_DIRECTORY="$1"
if [[ -L "$LVEF_PRIVATE_DIRECTORY" ]]; then
  printf '%s\n' 'lvef_private_directory_status=FAIL_SYMLINK' >&2
  exit 65
fi
if [[ ! -d "$LVEF_PRIVATE_DIRECTORY" ]]; then
  printf '%s\n' 'lvef_private_directory_status=FAIL_NOT_DIRECTORY' >&2
  exit 65
fi
if [[ ! -O "$LVEF_PRIVATE_DIRECTORY" ]]; then
  printf '%s\n' 'lvef_private_directory_status=FAIL_NOT_OWNER' >&2
  exit 65
fi

if LVEF_PRIVATE_DIRECTORY_MODE="$(stat -c '%a' "$LVEF_PRIVATE_DIRECTORY" 2>/dev/null)"; then
  :
elif LVEF_PRIVATE_DIRECTORY_MODE="$(stat -f '%Lp' "$LVEF_PRIVATE_DIRECTORY" 2>/dev/null)"; then
  :
else
  printf '%s\n' 'lvef_private_directory_status=FAIL_MODE_UNAVAILABLE' >&2
  exit 69
fi
case "$LVEF_PRIVATE_DIRECTORY_MODE" in
  700|2700)
    ;;
  *)
    printf '%s\n' 'lvef_private_directory_status=FAIL_NONPRIVATE_MODE' >&2
    exit 65
    ;;
esac
