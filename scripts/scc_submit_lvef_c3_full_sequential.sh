#!/bin/bash -p
set -euo pipefail
umask 077

export PATH=/usr/bin:/bin
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT

WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
PYTHON=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
SCHEDULER="$WORKTREE/scripts/lvef_c3_full_scheduler.py"
SCIENCE="$WORKTREE/scripts/lvef_c3_full_sequential.py"

[[ $# -eq 1 ]] || exit 64
case "$1" in
  --validate-installation|--preflight-only|--render|--submit) ;;
  --preflight-report)
    exec "$PYTHON" -I -B -X pycache_prefix=/dev/null/lvef_c3_full_report \
      "$SCIENCE" --preflight-report
    ;;
  *) exit 64 ;;
esac

exec "$PYTHON" -I -B -X pycache_prefix=/dev/null/lvef_c3_full_submitter \
  "$SCHEDULER" "$1"
