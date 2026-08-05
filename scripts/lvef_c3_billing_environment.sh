#!/usr/bin/env bash
# Scope the requester-pays project to the one command that needs it.

lvef_c3_quarantine_billing_project() {
  : "${LVEF_C3_GCP_BILLING_PROJECT:?LVEF_C3_GCP_BILLING_PROJECT is required}"
  # A sourced assignment can retain an export attribute inherited by this shell.
  # Remove that attribute before any unrelated subprocess is launched.
  export -n LVEF_C3_GCP_BILLING_PROJECT
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
