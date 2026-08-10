#!/usr/bin/env bash
set -euo pipefail
umask 077

test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"

SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test ! -L "$SESSION_ENV"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"

: "${WORKTREE:?}"
: "${EXPECTED_COMMIT:?}"
: "${PYTHON:?}"
: "${EXPECTED_PYTHON_SHA256:?}"
: "${RUN_ROOT:?}"
: "${PREFLIGHT_ENV:?}"
test "$WORKTREE" = "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"

cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test "$(git rev-parse origin/codex/lvef-multitask-revalidation)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain --untracked-files=no)"
CANONICAL_BILLING_HELPER="$WORKTREE/scripts/lvef_c3_billing_environment.sh"
test ! -L "$CANONICAL_BILLING_HELPER"
test -f "$CANONICAL_BILLING_HELPER"
test -O "$CANONICAL_BILLING_HELPER"
git ls-files --error-unmatch scripts/lvef_c3_billing_environment.sh >/dev/null
test "$(git hash-object "$CANONICAL_BILLING_HELPER")" = \
  "$(git rev-parse HEAD:scripts/lvef_c3_billing_environment.sh)"

test ! -L "$PREFLIGHT_ENV"
test -f "$PREFLIGHT_ENV"
test -O "$PREFLIGHT_ENV"
test "$(stat -c '%a' "$PREFLIGHT_ENV")" = "600"
source "$PREFLIGHT_ENV"
: "${SAFE_EXPORT_POLICY:?}"
: "${RESOURCE_POLICY:?}"
: "${GCLOUD:?}"
source "$CANONICAL_BILLING_HELPER"
lvef_c3_quarantine_gcp_authority_environment
test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"

test "$(sha256sum "$PYTHON" | awk '{print $1}')" = "$EXPECTED_PYTHON_SHA256"
test "$(sha256sum "$SAFE_EXPORT_POLICY" | awk '{print $1}')" = "$EXPECTED_SAFE_EXPORT_POLICY_SHA256"
test "$(sha256sum "$RESOURCE_POLICY" | awk '{print $1}')" = "$EXPECTED_RESOURCE_POLICY_SHA256"
: "${CLOUDSDK_CONFIG:?}"
"$WORKTREE/scripts/check_lvef_private_directory.sh" "$CLOUDSDK_CONFIG"
export CLOUDSDK_CONFIG

ADJUDICATION_POLICY="$WORKTREE/configs/lvef_c3_autoclass_adjudication.yaml"
EVIDENCE_REGISTRY="$WORKTREE/docs/lvef_multitask/c3_autoclass_official_evidence_registry.json"
GCP_AUTHORITY_RECEIPT="$RUN_ROOT/restricted/gcp_authority_receipt.restricted.json"
ORIGINAL_AGGREGATE_DIR="$RUN_ROOT/aggregate"
ATTEMPT_PARENT="$RUN_ROOT/autoclass_adjudication"
ATTEMPT_ROOT="$ATTEMPT_PARENT/phase1ebc_autoclass_adjudication_attempt_002"

test ! -L "$GCP_AUTHORITY_RECEIPT"
test -f "$GCP_AUTHORITY_RECEIPT"
test -O "$GCP_AUTHORITY_RECEIPT"
test "$(stat -c '%a' "$GCP_AUTHORITY_RECEIPT")" = "600"
GCP_AUTHORITY_RECEIPT_SHA256="$(sha256sum "$GCP_AUTHORITY_RECEIPT" | awk '{print $1}')"
[[ "$GCP_AUTHORITY_RECEIPT_SHA256" =~ ^[0-9a-f]{64}$ ]]

if [[ ! -e "$ATTEMPT_PARENT" ]]; then
  mkdir -m 700 "$ATTEMPT_PARENT"
fi
test ! -L "$ATTEMPT_PARENT"
test -d "$ATTEMPT_PARENT"
test -O "$ATTEMPT_PARENT"
ATTEMPT_PARENT_MODE="$(stat -c '%a' "$ATTEMPT_PARENT")"
test "$ATTEMPT_PARENT_MODE" = "700" || test "$ATTEMPT_PARENT_MODE" = "2700"
test ! -e "$ATTEMPT_ROOT"
test ! -L "$ATTEMPT_ROOT"

if lvef_c3_run_with_gcp_authority_environment \
  "$PYTHON" scripts/adjudicate_lvef_c3_autoclass.py capture \
    --attempt-root "$ATTEMPT_ROOT" \
    --governing-commit "$EXPECTED_COMMIT" \
    --safe-export-policy "$SAFE_EXPORT_POLICY" \
    --adjudication-policy "$ADJUDICATION_POLICY" \
    --official-evidence-registry "$EVIDENCE_REGISTRY" \
    --gcp-authority-receipt "$GCP_AUTHORITY_RECEIPT" \
    --expected-gcp-authority-receipt-sha256 "$GCP_AUTHORITY_RECEIPT_SHA256" \
    --gcloud-bin "$GCLOUD"; then
  :
else
  CAPTURE_STATUS=$?
  exit "$CAPTURE_STATUS"
fi
unset LVEF_C3_GCP_BILLING_PROJECT

"$PYTHON" scripts/adjudicate_lvef_c3_autoclass.py offline \
  --attempt-root "$ATTEMPT_ROOT" \
  --governing-commit "$EXPECTED_COMMIT" \
  --safe-export-policy "$SAFE_EXPORT_POLICY" \
  --adjudication-policy "$ADJUDICATION_POLICY" \
  --official-evidence-registry "$EVIDENCE_REGISTRY" \
  --original-aggregate-dir "$ORIGINAL_AGGREGATE_DIR" \
  --resource-policy "$RESOURCE_POLICY" \
  --selected-source-manifest "$SELECTED_SOURCE_MANIFEST" \
  --selected-studies "$SELECTED_STUDIES" \
  --split-map "$SPLIT_MAP"
