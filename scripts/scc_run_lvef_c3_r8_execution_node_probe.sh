#!/bin/bash -p
set -euo pipefail
umask 077

export PATH=/usr/bin:/bin
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT
export CUDA_VISIBLE_DEVICES=""

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
PYTHON=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
SCIENCE="$WORKTREE/scripts/lvef_c3_full_sequential.py"

[[ $# -eq 0 ]] || exit 64
[[ "${JOB_ID:-}" =~ ^[1-9][0-9]{0,19}$ ]] || exit 64
[[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 64
[[ "${NSLOTS:-1}" == "1" ]] || exit 64

cd "$WORKTREE"
exec "$PYTHON" -I -B -X pycache_prefix=/dev/null/lvef_c3_r8_probe \
  "$SCIENCE" --validate-execution-node-sealed-authorities
