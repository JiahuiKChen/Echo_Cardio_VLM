#!/bin/bash -p
set -euo pipefail
umask 077

export PATH=/usr/bin:/bin
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
PYTHON=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
CONTROLLER="$WORKTREE/scripts/lvef_c3_r8r_recovery_continuation.py"
COMMON="$WORKTREE/scripts/lvef_c3_production_scheduler_common.sh"
JOB_STORAGE_BASE=/restricted/projectnb/mimicecho/lvef_multitask_c3_v2/scheduler_runtime

[[ $# -eq 0 ]] || exit 64
[[ "${JOB_ID:-}" =~ ^[1-9][0-9]{0,19}$ ]] || exit 78
[[ "${JOB_NAME:-}" =~ ^lvef_c3_(r8r|r8u)_(rec|seq|fin)_[0-9a-f]{8}$ ]] || exit 78
[[ -f "$COMMON" && ! -L "$COMMON" ]] || exit 78
# shellcheck disable=SC1090 -- fixed authority-worktree helper path.
source "$COMMON"

case "$JOB_NAME" in
  lvef_c3_r8r_rec_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8r
    ROLE=recovery
    MODE=--recover-batch3-preservation
    PYCACHE_ROLE=lvef_c3_r8r
    ;;
  lvef_c3_r8r_seq_*)
    [[ "${SGE_TASK_ID:-}" =~ ^([4-9]|1[0-9])$ ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    JOB_FAMILY=r8r
    ROLE="array_task_${SGE_TASK_ID}"
    MODE=--run-continuation-array-task
    PYCACHE_ROLE=lvef_c3_r8r
    ;;
  lvef_c3_r8r_fin_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8r
    ROLE=finalizer
    MODE=--run-continuation-finalizer
    PYCACHE_ROLE=lvef_c3_r8r
    ;;
  lvef_c3_r8u_rec_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    JOB_FAMILY=r8u_r2
    ROLE=r8u_r2_batch16_recovery
    MODE=--run-batch16-recovery
    PYCACHE_ROLE=lvef_c3_r8u_r2
    ;;
  lvef_c3_r8u_seq_*)
    [[ "${SGE_TASK_ID:-}" =~ ^1[7-9]$ ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    JOB_FAMILY=r8u_r2
    ROLE="r8u_r2_array_task_${SGE_TASK_ID}"
    MODE=--run-continuation-17-19-array-task
    PYCACHE_ROLE=lvef_c3_r8u_r2
    ;;
  lvef_c3_r8u_fin_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r2
    ROLE=r8u_r2_finalizer
    MODE=--run-r8u-continuation-finalizer
    PYCACHE_ROLE=lvef_c3_r8u_r2
    ;;
  *) exit 78 ;;
esac

JOB_STORAGE_PARENT="$JOB_STORAGE_BASE/${JOB_FAMILY}_job_$JOB_ID"
JOB_STORAGE_ROOT="$JOB_STORAGE_PARENT/$ROLE"
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

cd "$WORKTREE"
exec "$PYTHON" -I -B -X "pycache_prefix=/dev/null/$PYCACHE_ROLE" \
  "$CONTROLLER" "$MODE"
