#!/usr/bin/env bash
# Resolve Google Cloud CLI on SCC without reading authentication material.
#
# Stdout contains only the selected absolute executable path. Diagnostic output
# is status-only and goes to stderr. The restricted JSON record contains tool
# provenance, never an account, token, project, billing account, or credential
# path.

set -u
set -o pipefail

PINNED_COMMON_VERSION="579.0.0"
PINNED_COMMON_ROOT="/restricted/projectnb/mimicecho/tools/google-cloud-cli-${PINNED_COMMON_VERSION}"
PINNED_ARCHIVE_NAME="google-cloud-cli-${PINNED_COMMON_VERSION}-linux-x86_64.tar.gz"
PINNED_ARCHIVE_BYTES="96066973"
PINNED_ARCHIVE_SHA256="a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de"
PINNED_TAR_PAYLOAD_SHA256="f44705777ec8b5b401ff705c39421f747780b7fb7655f836af43e316964b90bd"
DEFAULT_MODULE="google-cloud-sdk/455.0.0"
PREFERRED_LVEF_SCC_PYTHON="/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python"

usage() {
  printf '%s\n' \
    "Usage: $0 --record-json ABSOLUTE_PATH [--expected-version VERSION]" \
    "" \
    "Resolution order:" \
    "  1. LVEF_SCC_GCLOUD (absolute explicit path)" \
    "  2. gcloud already on PATH" \
    "  3. known project/user self-contained installations" \
    "  4. LVEF_SCC_GCLOUD_MODULE or ${DEFAULT_MODULE}" \
    "" \
    "Optional environment:" \
    "  LVEF_SCC_GCLOUD=/absolute/path/to/gcloud" \
    "  LVEF_SCC_GCLOUD_EXPECTED_VERSION=579.0.0" \
    "  LVEF_SCC_GCLOUD_MODULE=google-cloud-sdk/455.0.0" \
    "  LVEF_SCC_PYTHON=/absolute/path/to/pinned/python"
}

fail() {
  printf 'lvef_scc_gcloud_status=FAIL\n' >&2
  printf 'lvef_scc_gcloud_reason=%s\n' "$1" >&2
  return "${2:-2}"
}

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "$value"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk 'NR == 1 {print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk 'NR == 1 {print $1}'
  else
    return 69
  fi
}

record_json=""
expected_version="${LVEF_SCC_GCLOUD_EXPECTED_VERSION:-}"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --record-json)
      [[ "$#" -ge 2 ]] || { usage >&2; exit 64; }
      record_json="$2"
      shift 2
      ;;
    --expected-version)
      [[ "$#" -ge 2 ]] || { usage >&2; exit 64; }
      expected_version="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 64
      ;;
  esac
done

if [[ -z "$record_json" || "$record_json" != /* ]]; then
  fail "record_json_must_be_an_absolute_path" 64
  exit $?
fi
if [[ -e "$record_json" ]]; then
  fail "record_json_already_exists" 73
  exit $?
fi
record_parent="${record_json%/*}"
[[ -n "$record_parent" ]] || record_parent="/"
if [[ ! -d "$record_parent" || ! -w "$record_parent" ]]; then
  fail "record_json_parent_unavailable" 73
  exit $?
fi
if [[ -n "$expected_version" && ! "$expected_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  fail "expected_version_is_invalid" 64
  exit $?
fi

resolver_python="${LVEF_SCC_PYTHON:-$PREFERRED_LVEF_SCC_PYTHON}"
if [[ "$resolver_python" != /* || ! -f "$resolver_python" || ! -x "$resolver_python" ]]; then
  fail "pinned_python_is_required_for_gcloud_json_version_probe" 69
  exit $?
fi

candidate=""
resolution_source=""
module_name=""

if [[ -n "${LVEF_SCC_GCLOUD:-}" ]]; then
  candidate="$LVEF_SCC_GCLOUD"
  resolution_source="EXPLICIT_ABSOLUTE_PATH"
  if [[ "$candidate" != /* ]]; then
    fail "explicit_gcloud_path_must_be_absolute" 64
    exit $?
  fi
  if [[ ! -f "$candidate" || ! -x "$candidate" ]]; then
    fail "explicit_gcloud_path_is_not_executable_no_fallback" 69
    exit $?
  fi
elif command -v gcloud >/dev/null 2>&1; then
  candidate="$(command -v gcloud)"
  resolution_source="PATH"
else
  common_candidates=(
    "$PINNED_COMMON_ROOT/google-cloud-sdk/bin/gcloud"
    "${HOME:-/nonexistent}/google-cloud-sdk/bin/gcloud"
    "${HOME:-/nonexistent}/.local/google-cloud-sdk/bin/gcloud"
  )
  for common_candidate in "${common_candidates[@]}"; do
    if [[ -f "$common_candidate" && -x "$common_candidate" ]]; then
      candidate="$common_candidate"
      resolution_source="COMMON_SELF_CONTAINED_INSTALL"
      break
    fi
  done

  if [[ -z "$candidate" ]]; then
    # Environment Modules may be initialized only in an interactive login
    # shell. Sourcing a standard initializer affects this resolver subprocess
    # only; it does not edit a profile or the caller's environment.
    if ! type module >/dev/null 2>&1; then
      for initializer in /etc/profile.d/modules.sh /usr/share/Modules/init/bash; do
        if [[ -r "$initializer" ]]; then
          # shellcheck disable=SC1090
          source "$initializer" >/dev/null 2>&1 || true
          type module >/dev/null 2>&1 && break
        fi
      done
    fi
    module_name="${LVEF_SCC_GCLOUD_MODULE:-$DEFAULT_MODULE}"
    if type module >/dev/null 2>&1 && \
       module load "$module_name" >/dev/null 2>&1 && \
       command -v gcloud >/dev/null 2>&1; then
      candidate="$(command -v gcloud)"
      resolution_source="VERSIONED_ENVIRONMENT_MODULE"
    fi
  fi
fi

if [[ -z "$candidate" ]]; then
  fail "gcloud_not_found_prepare_pinned_bootstrap_only" 69
  exit $?
fi
case "$candidate" in
  *$'\n'*|*$'\r'*|*$'\t'*)
    fail "gcloud_candidate_contains_control_character" 64
    exit $?
    ;;
esac

if ! candidate="$("$resolver_python" -c '
from pathlib import Path
import sys
path = Path(sys.argv[1])
resolved = path.resolve(strict=True)
if not resolved.is_file():
    raise SystemExit(69)
print(resolved)
' "$candidate" 2>/dev/null)"; then
  fail "gcloud_candidate_cannot_be_canonicalized" 69
  exit $?
fi
if [[ "$candidate" != /* || ! -f "$candidate" || ! -x "$candidate" ]]; then
  fail "selected_gcloud_is_not_an_executable_file" 69
  exit $?
fi

version_config_parent="${TMPDIR:-/tmp}"
if [[ ! -d "$version_config_parent" || ! -w "$version_config_parent" ]]; then
  fail "gcloud_version_probe_temp_parent_unavailable" 73
  exit $?
fi
version_config="$(mktemp -d "$version_config_parent/lvef-gcloud-version.XXXXXX")"
chmod 700 "$version_config"
if ! version_json="$(CLOUDSDK_CONFIG="$version_config" "$candidate" version --format=json 2>/dev/null)"; then
  rm -rf -- "$version_config"
  fail "selected_gcloud_version_probe_failed" 69
  exit $?
fi
if ! version_text="$(
  printf '%s' "$version_json" | "$resolver_python" -c '
import json, sys
payload = json.load(sys.stdin)
value = payload.get("Google Cloud SDK")
if not isinstance(value, str):
    raise SystemExit(65)
print(value)
' 2>/dev/null
)"; then
  unset version_json
  rm -rf -- "$version_config"
  fail "selected_gcloud_version_json_is_invalid" 69
  exit $?
fi
unset version_json
rm -rf -- "$version_config"
if [[ ! "$version_text" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  fail "selected_gcloud_reported_invalid_version" 69
  exit $?
fi
if [[ -n "$expected_version" && "$version_text" != "$expected_version" ]]; then
  fail "selected_gcloud_version_mismatch" 65
  exit $?
fi
if ! executable_sha256="$(sha256_file "$candidate")" || \
   [[ ! "$executable_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "selected_gcloud_sha256_unavailable" 69
  exit $?
fi

retained_archive_sha256=""
retained_tar_payload_sha256=""
if [[ "$candidate" = "$PINNED_COMMON_ROOT/google-cloud-sdk/bin/gcloud" ]]; then
  retained_archive="$PINNED_COMMON_ROOT/$PINNED_ARCHIVE_NAME"
  if [[ ! -f "$retained_archive" ]] || \
     [[ "$(stat -c '%s' "$retained_archive")" != "$PINNED_ARCHIVE_BYTES" ]] || \
     ! retained_archive_sha256="$(sha256_file "$retained_archive")" || \
     [[ "$retained_archive_sha256" != "$PINNED_ARCHIVE_SHA256" ]] || \
     ! retained_tar_payload_sha256="$(gzip -dc "$retained_archive" | sha256sum | awk 'NR == 1 {print $1}')" || \
     [[ "$retained_tar_payload_sha256" != "$PINNED_TAR_PAYLOAD_SHA256" ]]; then
    fail "pinned_common_install_archive_authority_invalid" 65
    exit $?
  fi
fi

temporary_record="$record_parent/.${record_json##*/}.tmp.$$"
if [[ -e "$temporary_record" ]]; then
  fail "temporary_record_collision" 73
  exit $?
fi
umask 077
{
  printf '{\n'
  printf '  "audit": "lvef_scc_gcloud_resolution",\n'
  printf '  "credential_material_accessed": false,\n'
  printf '  "expected_version": '
  if [[ -n "$expected_version" ]]; then
    printf '"%s",\n' "$(json_escape "$expected_version")"
  else
    printf 'null,\n'
  fi
  printf '  "executable_sha256": "%s",\n' "$executable_sha256"
  printf '  "module_name": '
  if [[ "$resolution_source" == "VERSIONED_ENVIRONMENT_MODULE" ]]; then
    printf '"%s",\n' "$(json_escape "$module_name")"
  else
    printf 'null,\n'
  fi
  printf '  "resolution_source": "%s",\n' "$resolution_source"
  printf '  "retained_archive_sha256": '
  if [[ -n "$retained_archive_sha256" ]]; then
    printf '"%s",\n' "$retained_archive_sha256"
  else
    printf 'null,\n'
  fi
  printf '  "retained_tar_payload_sha256": '
  if [[ -n "$retained_tar_payload_sha256" ]]; then
    printf '"%s",\n' "$retained_tar_payload_sha256"
  else
    printf 'null,\n'
  fi
  printf '  "selected_executable": "%s",\n' "$(json_escape "$candidate")"
  printf '  "status": "PASS",\n'
  printf '  "version": "%s"\n' "$version_text"
  printf '}\n'
} >"$temporary_record"
chmod 600 "$temporary_record"
if ! mv "$temporary_record" "$record_json"; then
  fail "gcloud_validation_record_cannot_be_finalized" 73
  exit $?
fi
chmod 600 "$record_json"

printf 'lvef_scc_gcloud_status=PASS\n' >&2
printf 'lvef_scc_gcloud_resolution_source=%s\n' "$resolution_source" >&2
printf 'lvef_scc_gcloud_version=%s\n' "$version_text" >&2
printf '%s\n' "$candidate"
