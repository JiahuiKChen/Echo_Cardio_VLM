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
[[ "${JOB_NAME:-}" =~ ^lvef_c3_(r8r_(rec|seq|fin)|r8u_(rec|seq|fin)|r8u_r3_(res|seq|fin)|r8u_r4_(res|seq|fin)|r8u_r5_(ctx|res|seq|fin)|r8u_r6_(loc|res|seq|fin)|r8u_r7_(rec|seq|fin)|r8u_r7d_(ctx|seq|fin))_[0-9a-f]{8}$ ]] || exit 78
[[ -f "$COMMON" && ! -L "$COMMON" ]] || exit 78
# shellcheck disable=SC1090 -- fixed authority-worktree helper path.
source "$COMMON"

# R8U-R5/R6/R7 compute dispatch must not depend on an NSS username lookup before
# the Python worker establishes its sealed/kernel scheduler context.
# Historical runners retain their fixed name-based helper; R5 through R7 use the
# numeric effective UID exposed by the kernel through Bash's read-only EUID.
r8u_r5_require_private_projectnb_directory() {
  local candidate="$1"
  lvef_c3_require_projectnb_path "$candidate"
  [[ -d "$candidate" && ! -L "$candidate" ]] || \
    lvef_c3_die PRIVATE_DIRECTORY_INVALID
  [[ "$(stat -c '%u' "$candidate")" == "$EUID" ]] || \
    lvef_c3_die PRIVATE_DIRECTORY_WRONG_OWNER
  lvef_c3_private_directory_mode_ok "$(stat -c '%a' "$candidate")" || \
    lvef_c3_die PRIVATE_DIRECTORY_WRONG_MODE
}

require_job_private_projectnb_directory() {
  if [[ "$JOB_FAMILY" == r8u_r5 ]]; then
    r8u_r5_require_private_projectnb_directory "$1"
  elif [[ "$JOB_FAMILY" == r8u_r6 || "$JOB_FAMILY" == r8u_r7 || "$JOB_FAMILY" == r8u_r7d ]]; then
    r8u_r5_require_private_projectnb_directory "$1"
  else
    lvef_c3_require_private_projectnb_directory "$1"
  fi
}

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
  lvef_c3_r8u_r3_res_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    JOB_FAMILY=r8u_r3
    ROLE=r8u_r3_batch16_publication_resume
    MODE=--run-r8u-r3-batch16-publication-resume
    PYCACHE_ROLE=lvef_c3_r8u_r3
    ;;
  lvef_c3_r8u_r3_seq_*)
    [[ "${SGE_TASK_ID:-}" =~ ^1[7-9]$ ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    JOB_FAMILY=r8u_r3
    ROLE="r8u_r3_array_task_${SGE_TASK_ID}"
    MODE=--run-r8u-r3-continuation-17-19-array-task
    PYCACHE_ROLE=lvef_c3_r8u_r3
    ;;
  lvef_c3_r8u_r3_fin_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r3
    ROLE=r8u_r3_finalizer
    MODE=--run-r8u-r3-continuation-finalizer
    PYCACHE_ROLE=lvef_c3_r8u_r3
    ;;
  lvef_c3_r8u_r4_res_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    JOB_FAMILY=r8u_r4
    ROLE=r8u_r4_batch16_publication_resume
    MODE=--run-r8u-r4-batch16-publication-resume
    PYCACHE_ROLE=lvef_c3_r8u_r4
    ;;
  lvef_c3_r8u_r4_seq_*)
    [[ "${SGE_TASK_ID:-}" =~ ^1[7-9]$ ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    JOB_FAMILY=r8u_r4
    ROLE="r8u_r4_array_task_${SGE_TASK_ID}"
    MODE=--run-r8u-r4-continuation-17-19-array-task
    PYCACHE_ROLE=lvef_c3_r8u_r4
    ;;
  lvef_c3_r8u_r4_fin_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r4
    ROLE=r8u_r4_finalizer
    MODE=--run-r8u-r4-continuation-finalizer
    PYCACHE_ROLE=lvef_c3_r8u_r4
    ;;
  lvef_c3_r8u_r5_ctx_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "1" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r5
    ROLE=r8u_r5_worker_context_probe
    MODE=--run-r8u-r5-worker-context-probe
    PYCACHE_ROLE=lvef_c3_r8u_r5
    ;;
  lvef_c3_r8u_r5_res_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]] || exit 78
    JOB_FAMILY=r8u_r5
    ROLE=r8u_r5_batch16_publication_resume
    MODE=--run-r8u-r5-batch16-publication-resume
    PYCACHE_ROLE=lvef_c3_r8u_r5
    ;;
  lvef_c3_r8u_r5_seq_*)
    [[ "${SGE_TASK_ID:-}" =~ ^1[7-9]$ ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    JOB_FAMILY=r8u_r5
    ROLE="r8u_r5_array_task_${SGE_TASK_ID}"
    MODE=--run-r8u-r5-continuation-17-19-array-task
    PYCACHE_ROLE=lvef_c3_r8u_r5
    ;;
  lvef_c3_r8u_r5_fin_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r5
    ROLE=r8u_r5_finalizer
    MODE=--run-r8u-r5-continuation-finalizer
    PYCACHE_ROLE=lvef_c3_r8u_r5
    ;;
  lvef_c3_r8u_r6_loc_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "1" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r6
    ROLE=r8u_r6_locality_sequence_probe
    MODE=--run-r8u-r6-locality-sequence-probe
    PYCACHE_ROLE=lvef_c3_r8u_r6
    ;;
  lvef_c3_r8u_r6_res_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]] || exit 78
    JOB_FAMILY=r8u_r6
    ROLE=r8u_r6_batch16_publication_resume
    MODE=--run-r8u-r6-batch16-publication-resume
    PYCACHE_ROLE=lvef_c3_r8u_r6
    ;;
  lvef_c3_r8u_r6_seq_*)
    [[ "${SGE_TASK_ID:-}" =~ ^1[7-9]$ ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]] || exit 78
    JOB_FAMILY=r8u_r6
    ROLE="r8u_r6_array_task_${SGE_TASK_ID}"
    MODE=--run-r8u-r6-continuation-17-19-array-task
    PYCACHE_ROLE=lvef_c3_r8u_r6
    ;;
  lvef_c3_r8u_r6_fin_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r6
    ROLE=r8u_r6_finalizer
    MODE=--run-r8u-r6-continuation-finalizer
    PYCACHE_ROLE=lvef_c3_r8u_r6
    ;;
  lvef_c3_r8u_r7_rec_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r7
    ROLE=r8u_r7_batch16_preservation_recovery
    MODE=--run-r8u-r7-batch16-preservation-recovery
    PYCACHE_ROLE=lvef_c3_r8u_r7
    ;;
  lvef_c3_r8u_r7_seq_*)
    [[ "${SGE_TASK_ID:-}" =~ ^1[7-9]$ ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]] || exit 78
    JOB_FAMILY=r8u_r7
    ROLE="r8u_r7_array_task_${SGE_TASK_ID}"
    MODE=--run-r8u-r7-continuation-17-19-array-task
    PYCACHE_ROLE=lvef_c3_r8u_r7
    ;;
  lvef_c3_r8u_r7_fin_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r7
    ROLE=r8u_r7_finalizer
    MODE=--run-r8u-r7-continuation-finalizer
    PYCACHE_ROLE=lvef_c3_r8u_r7
    ;;
  lvef_c3_r8u_r7d_ctx_*)
    [[ "${SGE_TASK_ID:-}" == "17" ]] || exit 78
    [[ "${NSLOTS:-}" == "1" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r7d
    ROLE=r8u_r7d_context_probe
    MODE=--run-r8u-r7d-continuation-context-probe
    PYCACHE_ROLE=lvef_c3_r8u_r7d
    ;;
  lvef_c3_r8u_r7d_seq_*)
    [[ "${SGE_TASK_ID:-}" =~ ^1[7-9]$ ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]] || exit 78
    JOB_FAMILY=r8u_r7d
    ROLE="r8u_r7d_array_task_${SGE_TASK_ID}"
    MODE=--run-r8u-r7d-continuation-17-19-array-task
    PYCACHE_ROLE=lvef_c3_r8u_r7d
    ;;
  lvef_c3_r8u_r7d_fin_*)
    [[ "${SGE_TASK_ID:-undefined}" == "undefined" ]] || exit 78
    [[ "${NSLOTS:-}" == "4" ]] || exit 78
    export CUDA_VISIBLE_DEVICES=''
    JOB_FAMILY=r8u_r7d
    ROLE=r8u_r7d_finalizer
    MODE=--run-r8u-r7d-continuation-finalizer
    PYCACHE_ROLE=lvef_c3_r8u_r7d
    ;;
  *) exit 78 ;;
esac

if [[ "$ROLE" != r8u_r7d_context_probe ]]; then
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
    require_job_private_projectnb_directory "$storage_directory"
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
    require_job_private_projectnb_directory "$storage_directory"
  done
fi

cd "$WORKTREE"
exec "$PYTHON" -I -B -X "pycache_prefix=/dev/null/$PYCACHE_ROLE" \
  "$CONTROLLER" "$MODE"
