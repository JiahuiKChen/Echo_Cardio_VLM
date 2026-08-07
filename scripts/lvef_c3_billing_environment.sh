#!/usr/bin/env bash
# Scope the requester-pays project to the one command that needs it.

lvef_c3_quarantine_billing_project() {
  : "${LVEF_C3_GCP_BILLING_PROJECT:?LVEF_C3_GCP_BILLING_PROJECT is required}"
  # A sourced assignment can retain an export attribute inherited by this shell.
  # Remove that attribute before any unrelated subprocess is launched.
  export -n LVEF_C3_GCP_BILLING_PROJECT
}

lvef_c3_quarantine_gcp_authority_environment() {
  : "${LVEF_C3_GCP_BILLING_PROJECT:?LVEF_C3_GCP_BILLING_PROJECT is required}"
  : "${LVEF_C3_EXPECTED_GCP_ACCOUNT:?LVEF_C3_EXPECTED_GCP_ACCOUNT is required}"
  : "${LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME:?LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME is required}"
  : "${EXPECTED_COMMIT:?EXPECTED_COMMIT is required}"
  : "${RUN_ROOT:?RUN_ROOT is required}"
  : "${GCP_AUTHORITY_WRAPPER:?GCP_AUTHORITY_WRAPPER is required}"
  : "${EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256:?EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256 is required}"
  : "${C3_EXECUTION_CONTRACT:?C3_EXECUTION_CONTRACT is required}"
  : "${EXPECTED_C3_EXECUTION_CONTRACT_SHA256:?EXPECTED_C3_EXECUTION_CONTRACT_SHA256 is required}"
  : "${EXPECTED_SELECTED_SOURCE_SHA256:?EXPECTED_SELECTED_SOURCE_SHA256 is required}"
  : "${GCLOUD_RESOLVER:?GCLOUD_RESOLVER is required}"
  : "${EXPECTED_GCLOUD_RESOLVER_SHA256:?EXPECTED_GCLOUD_RESOLVER_SHA256 is required}"
  : "${GCP_QUOTA_PROJECT_STAGE:?GCP_QUOTA_PROJECT_STAGE is required}"
  : "${EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256:?EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256 is required}"
  export -n LVEF_C3_GCP_BILLING_PROJECT
  export -n LVEF_C3_EXPECTED_GCP_ACCOUNT
  export -n LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME
  export -n EXPECTED_COMMIT RUN_ROOT GCP_AUTHORITY_WRAPPER
  export -n EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256 C3_EXECUTION_CONTRACT
  export -n EXPECTED_C3_EXECUTION_CONTRACT_SHA256 EXPECTED_SELECTED_SOURCE_SHA256
  export -n GCLOUD_RESOLVER EXPECTED_GCLOUD_RESOLVER_SHA256
  export -n GCP_QUOTA_PROJECT_STAGE EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256
  if [[ -n "${GCLOUD_RESOLUTION_RECORD:-}" ]]; then
    export -n GCLOUD GCLOUD_RESOLUTION_RECORD EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256
  fi
  if [[ -n "${LVEF_C3_GCP_AUTHORIZED_USER_FILE:-}" ]]; then
    export -n LVEF_C3_GCP_AUTHORIZED_USER_FILE
  fi
}

lvef_c3_run_with_gcp_authority_environment() {
  local lvef_c3_command_status

  lvef_c3_quarantine_gcp_authority_environment
  # Prefix assignments export only to this command.  The caller's copies keep
  # their export attributes removed and can be used for a later gated resume.
  if LVEF_C3_GCP_BILLING_PROJECT="$LVEF_C3_GCP_BILLING_PROJECT" \
    LVEF_C3_EXPECTED_GCP_ACCOUNT="$LVEF_C3_EXPECTED_GCP_ACCOUNT" \
    LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME="$LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME" \
    LVEF_C3_GCP_AUTHORIZED_USER_FILE="${LVEF_C3_GCP_AUTHORIZED_USER_FILE:-}" \
    EXPECTED_COMMIT="$EXPECTED_COMMIT" \
    RUN_ROOT="$RUN_ROOT" \
    GCLOUD="${GCLOUD:-}" \
    GCLOUD_RESOLUTION_RECORD="${GCLOUD_RESOLUTION_RECORD:-}" \
    EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256="${EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256:-}" \
    GCP_AUTHORITY_WRAPPER="$GCP_AUTHORITY_WRAPPER" \
    EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256="$EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256" \
    C3_EXECUTION_CONTRACT="$C3_EXECUTION_CONTRACT" \
    EXPECTED_C3_EXECUTION_CONTRACT_SHA256="$EXPECTED_C3_EXECUTION_CONTRACT_SHA256" \
    EXPECTED_SELECTED_SOURCE_SHA256="$EXPECTED_SELECTED_SOURCE_SHA256" \
    GCLOUD_RESOLVER="$GCLOUD_RESOLVER" \
    EXPECTED_GCLOUD_RESOLVER_SHA256="$EXPECTED_GCLOUD_RESOLVER_SHA256" \
    GCP_QUOTA_PROJECT_STAGE="$GCP_QUOTA_PROJECT_STAGE" \
    EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256="$EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256" \
    "$@"; then
    lvef_c3_command_status=0
  else
    lvef_c3_command_status=$?
  fi
  return "$lvef_c3_command_status"
}

lvef_c3_run_with_billing_project() {
  local lvef_c3_billing_project_value
  local lvef_c3_command_status

  : "${LVEF_C3_GCP_BILLING_PROJECT:?LVEF_C3_GCP_BILLING_PROJECT is required}"
  lvef_c3_billing_project_value="$LVEF_C3_GCP_BILLING_PROJECT"
  unset LVEF_C3_GCP_BILLING_PROJECT

  # Prefix assignment exports the value only to this command without placing it
  # in argv. Capture failures explicitly so the local copy is cleared first.
  if LVEF_C3_GCP_BILLING_PROJECT="$lvef_c3_billing_project_value" "$@"; then
    lvef_c3_command_status=0
  else
    lvef_c3_command_status=$?
  fi

  unset lvef_c3_billing_project_value
  return "$lvef_c3_command_status"
}
