#!/bin/bash -p
set -euo pipefail
umask 077

export PATH=/usr/bin:/bin
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_TERMINAL_PROMPT=0
export PYTHONDONTWRITEBYTECODE=1
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
unset PYTHONPATH PYTHONHOME PYTHONINSPECT PYTHONSTARTUP
unset CLOUDSDK_CONFIG GOOGLE_APPLICATION_CREDENTIALS

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
ECHOPRIME_PYTHON=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
EXPECTED_PYTHON_SHA256=1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb
[[ $# -ge 1 && $# -le 2 ]] || exit 64
case "$1:$#" in
  --validate-installation:1|--preflight-only:1|--preflight-live-authority:1) ;;
  --validate-sealed-manifest:2|--claim-sealed-manifest:2|--run-sealed-manifest:2) ;;
  *) exit 64 ;;
esac
if [[ "$1" = --run-sealed-manifest ]]; then
  : # Preserve SGE's inherited one-GPU device affinity for the queued job.
else
  export CUDA_VISIBLE_DEVICES=''
fi
[[ -d "$WORKTREE" && ! -L "$WORKTREE" ]] || exit 65
[[ -f "$WORKTREE/scripts/lvef_c3_minimal_canary.py" && ! -L "$WORKTREE/scripts/lvef_c3_minimal_canary.py" ]] || exit 65
resolved_python=$(/usr/bin/readlink -f -- "$ECHOPRIME_PYTHON") || exit 65
[[ -f "$resolved_python" && ! -L "$resolved_python" ]] || exit 65
read -r observed_sha _ < <(/usr/bin/sha256sum -- "$resolved_python") || exit 65
[[ "$observed_sha" = "$EXPECTED_PYTHON_SHA256" ]] || exit 65

exec "$ECHOPRIME_PYTHON" -I -B -X pycache_prefix=/dev/null/lvef_c3_minimal \
  "$WORKTREE/scripts/lvef_c3_minimal_canary.py" "$@"
