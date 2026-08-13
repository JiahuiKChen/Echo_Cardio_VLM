#!/bin/bash -p
set -euo pipefail
umask 077

export PATH=/usr/bin:/bin
unset BASH_ENV ENV CDPATH

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
RUNNER="$WORKTREE/scripts/scc_run_lvef_c3_minimal_canary.sh"
QSUB=/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64/qsub
[[ $# -ge 1 && $# -le 2 ]] || exit 64

case "$1:$#" in
  --validate-installation:1|--preflight-only:1|--preflight-live-authority:1)
    exec "$RUNNER" "$1"
    ;;
  --submit:2)
    manifest=$2
    [[ "$manifest" = /* ]] || exit 64
    "$RUNNER" --validate-installation
    "$RUNNER" --preflight-live-authority
    "$RUNNER" --validate-sealed-manifest "$manifest"
    "$RUNNER" --claim-sealed-manifest "$manifest"
    [[ -x "$QSUB" && ! -L "$QSUB" ]] || exit 65
    exec "$QSUB" -terse -r n -P mimicecho -N lvef_c3_minimal_canary -j y \
      -o /restricted/projectnb/mimicecho/lvef_multitask_c3_v2 \
      -l h_rt=48:00:00 -l gpus=1 -l gpu_c=8.0 -l gpu_memory=48G \
      -pe omp 4 -l mem_per_core=16G \
      "$RUNNER" --run-sealed-manifest "$manifest"
    ;;
  *) exit 64 ;;
esac
