#!/bin/bash -p
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
PYTHON=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
WORKER="$WORKTREE/scripts/lvef_c3_minimal_canary_preservation_recovery.py"

[[ $# -eq 1 ]] || exit 64
case "$1:$#" in
  --validate-installation:1|--preflight-only:1|--submit:1) ;;
  *) exit 64 ;;
esac
[[ -d "$WORKTREE" && ! -L "$WORKTREE" ]] || exit 65
[[ -f "$WORKER" && ! -L "$WORKER" ]] || exit 65
resolved_python=$(/usr/bin/readlink -f -- "$PYTHON") || exit 65
[[ -f "$resolved_python" && ! -L "$resolved_python" ]] || exit 65

exec "$PYTHON" -I -B -X pycache_prefix=/dev/null/lvef_c3_recovery \
  "$WORKER" "$1"
