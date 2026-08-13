#!/bin/bash -p
# DEPRECATED/NONCONTROLLING: retained for historical comparison only.
# The controlling future route is scc_submit_lvef_c3_minimal_canary.sh.
set -euo pipefail
umask 077

export PATH=/usr/bin:/bin
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_TERMINAL_PROMPT=0
export PYTHONDONTWRITEBYTECODE=1
export CUDA_VISIBLE_DEVICES=''
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
unset PYTHONPATH PYTHONHOME PYTHONINSPECT PYTHONSTARTUP
unset CLOUDSDK_CONFIG GOOGLE_APPLICATION_CREDENTIALS

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
ECHOPRIME_PYTHON=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
EXPECTED_PYTHON_SHA256=1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb
[[ $# -eq 1 ]] || exit 64
case "$1" in
  --validate-installation|--prepare-live-authority|--preflight-only|--execute) ;;
  *) exit 64 ;;
esac
[[ -d "$WORKTREE" && ! -L "$WORKTREE" ]] || exit 65
[[ -f "$WORKTREE/scripts/lvef_c3_canary.py" && ! -L "$WORKTREE/scripts/lvef_c3_canary.py" ]] || exit 65

cursor=/
IFS=/ read -r -a launcher_parts <<< "${ECHOPRIME_PYTHON#/}"
for ((index=0; index < ${#launcher_parts[@]} - 1; index++)); do
  cursor="${cursor%/}/${launcher_parts[index]}"
  [[ -d "$cursor" && ! -L "$cursor" ]] || exit 65
done
[[ -e "$ECHOPRIME_PYTHON" && ( -f "$ECHOPRIME_PYTHON" || -L "$ECHOPRIME_PYTHON" ) ]] || exit 65
resolved_python=$(/usr/bin/readlink -f -- "$ECHOPRIME_PYTHON" 2>/dev/null) || exit 65
[[ -n "$resolved_python" && -f "$resolved_python" && ! -L "$resolved_python" ]] || exit 65
resolved_mode=$(/usr/bin/stat -c %a -- "$resolved_python" 2>/dev/null) || exit 65
[[ "$resolved_mode" =~ ^[0-7]{3,4}$ ]] || exit 65
(( (8#$resolved_mode & 07000) == 0 && (8#$resolved_mode & 0500) == 0500 && (8#$resolved_mode & 0022) == 0 )) || exit 65
read -r resolved_sha _ < <(/usr/bin/sha256sum -- "$resolved_python" 2>/dev/null) || exit 65
[[ "$resolved_sha" = "$EXPECTED_PYTHON_SHA256" ]] || exit 65

exec "$ECHOPRIME_PYTHON" -I -B -X pycache_prefix=/dev/null/lvef_c3_canary \
  "$WORKTREE/scripts/lvef_c3_canary.py" "$1"
