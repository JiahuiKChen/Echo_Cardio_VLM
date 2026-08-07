#!/usr/bin/env bash
# Create or live-revalidate an immutable, identifier-free GCP authority receipt.
set -euo pipefail
umask 077

usage() {
  printf '%s\n' \
    'Usage: verify_lvef_scc_gcp_authority.sh --preflight-env FILE --restricted-output FILE --aggregate-output FILE' >&2
  exit 64
}

PREFLIGHT_ENV=''
RESTRICTED_OUTPUT=''
AGGREGATE_OUTPUT=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --preflight-env) PREFLIGHT_ENV="${2:-}"; shift 2 ;;
    --restricted-output) RESTRICTED_OUTPUT="${2:-}"; shift 2 ;;
    --aggregate-output) AGGREGATE_OUTPUT="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$PREFLIGHT_ENV" && -n "$RESTRICTED_OUTPUT" && -n "$AGGREGATE_OUTPUT" ]] || usage

test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -f "$PREFLIGHT_ENV"
test -O "$PREFLIGHT_ENV"
test "$(stat -c '%a' "$PREFLIGHT_ENV")" = '600'
# shellcheck disable=SC1090
source "$PREFLIGHT_ENV"

: "${WORKTREE:?}"
: "${EXPECTED_COMMIT:?}"
: "${PYTHON:?}"
: "${EXPECTED_PYTHON_SHA256:?}"
: "${RUN_ROOT:?}"
: "${SELECTED_SOURCE_MANIFEST:?}"
: "${EXPECTED_SELECTED_SOURCE_SHA256:?}"
: "${GCLOUD_RESOLVER:?}"
: "${EXPECTED_GCLOUD_RESOLVER_SHA256:?}"
: "${GCP_AUTHORITY_WRAPPER:?}"
: "${EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256:?}"
: "${C3_EXECUTION_CONTRACT:?}"
: "${EXPECTED_C3_EXECUTION_CONTRACT_SHA256:?}"
: "${LVEF_C3_EXPECTED_GCP_ACCOUNT:?}"
: "${LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME:?}"
: "${LVEF_C3_GCP_BILLING_PROJECT:?}"

cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
test -x "$PYTHON"
test "$(sha256sum "$PYTHON" | awk '{print $1}')" = "$EXPECTED_PYTHON_SHA256"
test "$GCP_AUTHORITY_WRAPPER" = "$WORKTREE/scripts/verify_lvef_scc_gcp_authority.sh"
test "$(sha256sum "$GCP_AUTHORITY_WRAPPER" | awk '{print $1}')" = "$EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256"
test "$(sha256sum "$C3_EXECUTION_CONTRACT" | awk '{print $1}')" = "$EXPECTED_C3_EXECUTION_CONTRACT_SHA256"
test "$(sha256sum "$SELECTED_SOURCE_MANIFEST" | awk '{print $1}')" = "$EXPECTED_SELECTED_SOURCE_SHA256"
test "$(sha256sum "$GCLOUD_RESOLVER" | awk '{print $1}')" = "$EXPECTED_GCLOUD_RESOLVER_SHA256"

GCLOUD="${GCLOUD:-}"
LVEF_C3_GCP_AUTHORIZED_USER_FILE="${LVEF_C3_GCP_AUTHORIZED_USER_FILE:-}"
if [[ -n "$GCLOUD" ]]; then
  test -z "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test -x "$GCLOUD"
  : "${GCLOUD_RESOLUTION_RECORD:?}"
  : "${EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256:?}"
  test "$(sha256sum "$GCLOUD_RESOLUTION_RECORD" | awk '{print $1}')" = "$EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256"
  : "${CLOUDSDK_CONFIG:?}"
  test -d "$CLOUDSDK_CONFIG"
  test -O "$CLOUDSDK_CONFIG"
  test "$(stat -c '%a' "$CLOUDSDK_CONFIG")" = '700'
  export CLOUDSDK_CONFIG
else
  test -n "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test -f "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test -O "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
  test "$(stat -c '%a' "$LVEF_C3_GCP_AUTHORIZED_USER_FILE")" = '600'
fi

RESTRICTED_EXISTS=0
AGGREGATE_EXISTS=0
[[ -e "$RESTRICTED_OUTPUT" ]] && RESTRICTED_EXISTS=1
[[ -e "$AGGREGATE_OUTPUT" ]] && AGGREGATE_EXISTS=1
if [[ "$RESTRICTED_EXISTS" -ne "$AGGREGATE_EXISTS" ]]; then
  printf '%s\n' 'PARTIAL_GCP_AUTHORITY_RECEIPT_SET' >&2
  exit 73
fi
MODE='create'
[[ "$RESTRICTED_EXISTS" -eq 1 ]] && MODE='validate'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lvef_c3_billing_environment.sh"
lvef_c3_quarantine_gcp_authority_environment
lvef_c3_run_with_gcp_authority_environment \
  "$PYTHON" "$SCRIPT_DIR/audit_lvef_c3_gcp_authority.py" "$MODE" \
  --gcloud-bin "$GCLOUD" \
  --source-manifest "$SELECTED_SOURCE_MANIFEST" \
  --wrapper-script "$GCP_AUTHORITY_WRAPPER" \
  --execution-contract "$C3_EXECUTION_CONTRACT" \
  --restricted-output "$RESTRICTED_OUTPUT" \
  --aggregate-output "$AGGREGATE_OUTPUT"

test -f "$RESTRICTED_OUTPUT"
test -O "$RESTRICTED_OUTPUT"
test "$(stat -c '%a' "$RESTRICTED_OUTPUT")" = '600'
test -f "$AGGREGATE_OUTPUT"
test -O "$AGGREGATE_OUTPUT"
test "$(stat -c '%a' "$AGGREGATE_OUTPUT")" = '600'
