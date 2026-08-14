#!/bin/bash -p
set -euo pipefail
umask 077

export PATH=/usr/bin:/bin
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
PYTHON=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
WORKER="$WORKTREE/scripts/lvef_c3_full_sequential.py"
COMMON="$WORKTREE/scripts/lvef_c3_production_scheduler_common.sh"
JOB_STORAGE_BASE=/restricted/projectnb/mimicecho/lvef_multitask_c3_v2/scheduler_runtime

[[ $# -eq 0 ]] || exit 64
: "${JOB_ID:?JOB_ID is required}"
: "${SGE_TASK_ID:?SGE_TASK_ID is required}"
[[ "$JOB_ID" =~ ^[1-9][0-9]{0,19}$ ]] || exit 78
[[ "$SGE_TASK_ID" =~ ^([1-9]|1[0-9])$ ]] || exit 78
[[ -f "$COMMON" && ! -L "$COMMON" ]] || exit 78
# shellcheck disable=SC1090 -- fixed authority-worktree helper path.
source "$COMMON"

JOB_STORAGE_PARENT="$JOB_STORAGE_BASE/array_job_$JOB_ID"
JOB_STORAGE_ROOT="$JOB_STORAGE_PARENT/task_$SGE_TASK_ID"
for storage_directory in \
  "$JOB_STORAGE_BASE" \
  "$JOB_STORAGE_PARENT" \
  "$JOB_STORAGE_ROOT"
do
  lvef_c3_require_projectnb_path "$storage_directory"
  mkdir -p "$storage_directory"
  lvef_c3_require_projectnb_path "$storage_directory"
  lvef_c3_require_private_projectnb_directory "$storage_directory"
done

export TMPDIR="$JOB_STORAGE_ROOT/tmp"
export XDG_CACHE_HOME="$JOB_STORAGE_ROOT/cache/xdg"
export TORCH_HOME="$JOB_STORAGE_ROOT/cache/torch"
export MPLCONFIGDIR="$JOB_STORAGE_ROOT/cache/matplotlib"
export NUMBA_CACHE_DIR="$JOB_STORAGE_ROOT/cache/numba"
export PIP_CACHE_DIR="$JOB_STORAGE_ROOT/cache/pip"
export JOBLIB_TEMP_FOLDER="$JOB_STORAGE_ROOT/tmp/joblib"
for storage_directory in \
  "$JOB_STORAGE_ROOT/cache" \
  "$TMPDIR" \
  "$XDG_CACHE_HOME" \
  "$TORCH_HOME" \
  "$MPLCONFIGDIR" \
  "$NUMBA_CACHE_DIR" \
  "$PIP_CACHE_DIR" \
  "$JOBLIB_TEMP_FOLDER"
do
  lvef_c3_require_projectnb_path "$storage_directory"
  mkdir -p "$storage_directory"
  lvef_c3_require_projectnb_path "$storage_directory"
  lvef_c3_require_private_projectnb_directory "$storage_directory"
done

exec "$PYTHON" -I -B -X pycache_prefix=/dev/null/lvef_c3_full_array \
  "$WORKER" --run-array-task
