#!/usr/bin/env bash
set -euo pipefail

: "${JDIM_REPO_ROOT:?}"
: "${JDIM_OUTPUT_ROOT:?}"
: "${JDIM_CORRECTED_ROOT:?}"
: "${JDIM_PHASE2_ROOT:?}"
: "${JDIM_DUPLICATE_DECISION_ROOT:?}"
: "${JDIM_SPLIT_MAP_CSV:?}"
: "${JDIM_RECON_ROOT:?}"
: "${JDIM_PYTHON_BIN:?}"
: "${JDIM_FAILED_RECON_ROOT:?}"
: "${JDIM_EXPECTED_SOURCE_COMMIT:?}"
: "${JDIM_EXPECTED_DRIVER_SHA256:?}"

"$JDIM_PYTHON_BIN" "$JDIM_REPO_ROOT/scripts/phase2er_reconcile.py" \
  --repo-root "$JDIM_REPO_ROOT" \
  --workflow-root "$JDIM_OUTPUT_ROOT" \
  --corrected-root "$JDIM_CORRECTED_ROOT" \
  --phase2-root "$JDIM_PHASE2_ROOT" \
  --decision-root "$JDIM_DUPLICATE_DECISION_ROOT" \
  --split-map-csv "$JDIM_SPLIT_MAP_CSV" \
  --reconciliation-root "$JDIM_RECON_ROOT" \
  --python-bin "$JDIM_PYTHON_BIN" \
  --failed-v1-root "$JDIM_FAILED_RECON_ROOT" \
  --expected-source-commit "$JDIM_EXPECTED_SOURCE_COMMIT" \
  --expected-driver-sha256 "$JDIM_EXPECTED_DRIVER_SHA256"
