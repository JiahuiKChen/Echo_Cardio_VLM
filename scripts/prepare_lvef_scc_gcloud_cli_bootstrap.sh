#!/usr/bin/env bash
# Prepare, but never execute, a pinned self-contained Google Cloud CLI bootstrap.

set -euo pipefail
umask 077

VERSION="579.0.0"
ARCHIVE_NAME="google-cloud-cli-${VERSION}-linux-x86_64.tar.gz"
ARCHIVE_URL="https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/${ARCHIVE_NAME}"
ARCHIVE_BYTES="96066973"
ARCHIVE_SHA256="a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de"
TAR_PAYLOAD_SHA256="f44705777ec8b5b401ff705c39421f747780b7fb7655f836af43e316964b90bd"
INSTALL_ROOT="/restricted/projectnb/mimicecho/tools/google-cloud-cli-${VERSION}"
PINNED_PYTHON="/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python"
PINNED_PYTHON_SHA256="1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"

usage() {
  printf '%s\n' \
    "Usage: $0 --output-script ABSOLUTE_PATH" \
    "" \
    "Writes a mode-600, checksum-pinned bootstrap script for separate owner" \
    "review. It never downloads, extracts, installs, authenticates, or executes" \
    "the generated script."
}

output_script=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --output-script)
      [[ "$#" -ge 2 ]] || { usage >&2; exit 64; }
      output_script="$2"
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

if [[ -z "$output_script" || "$output_script" != /* ]]; then
  printf '%s\n' 'gcloud_bootstrap_prepare_status=FAIL_OUTPUT_MUST_BE_ABSOLUTE' >&2
  exit 64
fi
if [[ -e "$output_script" ]]; then
  printf '%s\n' 'gcloud_bootstrap_prepare_status=FAIL_OUTPUT_ALREADY_EXISTS' >&2
  exit 73
fi
output_parent="${output_script%/*}"
[[ -n "$output_parent" ]] || output_parent="/"
if [[ ! -d "$output_parent" || ! -w "$output_parent" ]]; then
  printf '%s\n' 'gcloud_bootstrap_prepare_status=FAIL_OUTPUT_PARENT_UNAVAILABLE' >&2
  exit 73
fi

temporary_script="$output_parent/.${output_script##*/}.tmp.$$"
test ! -e "$temporary_script"
cat >"$temporary_script" <<EOF
#!/usr/bin/env bash
# Prepared only. Execute later solely after owner review and authorization.
set -euo pipefail
umask 077

VERSION='${VERSION}'
ARCHIVE_NAME='${ARCHIVE_NAME}'
ARCHIVE_URL='${ARCHIVE_URL}'
ARCHIVE_BYTES='${ARCHIVE_BYTES}'
ARCHIVE_SHA256='${ARCHIVE_SHA256}'
TAR_PAYLOAD_SHA256='${TAR_PAYLOAD_SHA256}'
INSTALL_ROOT='${INSTALL_ROOT}'
PINNED_PYTHON='${PINNED_PYTHON}'
PINNED_PYTHON_SHA256='${PINNED_PYTHON_SHA256}'
INSTALL_PARENT="\${INSTALL_ROOT%/*}"

test "\$INSTALL_PARENT" = '/restricted/projectnb/mimicecho/tools'
test "\$INSTALL_ROOT" = '/restricted/projectnb/mimicecho/tools/google-cloud-cli-579.0.0'
test ! -e "\$INSTALL_ROOT"
test -x "\$PINNED_PYTHON"
test "\$(sha256sum "\$PINNED_PYTHON" | awk '{print \$1}')" = "\$PINNED_PYTHON_SHA256"
mkdir -p "\$INSTALL_PARENT"
STAGING_ROOT="\$(mktemp -d "\$INSTALL_PARENT/.google-cloud-cli-579.0.0.bootstrap.XXXXXX")"
test -d "\$STAGING_ROOT"
ARCHIVE_PATH="\$STAGING_ROOT/\$ARCHIVE_NAME"

curl --fail --show-error --silent --location \
  --proto '=https' --tlsv1.2 \
  --output "\$ARCHIVE_PATH" "\$ARCHIVE_URL"
test "\$(stat -c '%s' "\$ARCHIVE_PATH")" = "\$ARCHIVE_BYTES"
printf '%s  %s\n' "\$ARCHIVE_SHA256" "\$ARCHIVE_PATH" | sha256sum --check --strict -
test "\$(gzip -dc "\$ARCHIVE_PATH" | sha256sum | awk '{print \$1}')" = "\$TAR_PAYLOAD_SHA256"
tar -tzf "\$ARCHIVE_PATH" >"\$STAGING_ROOT/archive_members.txt"
if grep -Eq '(^/|(^|/)\.\.(/|\$))' "\$STAGING_ROOT/archive_members.txt"; then
  printf '%s\n' 'gcloud_bootstrap_status=FAIL_UNSAFE_ARCHIVE_MEMBER' >&2
  exit 65
fi
tar -xzf "\$ARCHIVE_PATH" -C "\$STAGING_ROOT"
GCLOUD_STAGED="\$STAGING_ROOT/google-cloud-sdk/bin/gcloud"
test -f "\$GCLOUD_STAGED"
test -x "\$GCLOUD_STAGED"
mkdir "\$STAGING_ROOT/version_config"
GCLOUD_VERSION_JSON="\$(CLOUDSDK_CONFIG="\$STAGING_ROOT/version_config" "\$GCLOUD_STAGED" version --format=json 2>/dev/null)"
GCLOUD_VERSION="\$(printf '%s' "\$GCLOUD_VERSION_JSON" | "\$PINNED_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["Google Cloud SDK"])')"
unset GCLOUD_VERSION_JSON
test "\$GCLOUD_VERSION" = "\$VERSION"

# Preserve the downloaded archive and member inventory beside the SDK as
# checksum evidence. No shell profile, system package, or external path changes.
mv "\$STAGING_ROOT" "\$INSTALL_ROOT"
GCLOUD_FINAL="\$INSTALL_ROOT/google-cloud-sdk/bin/gcloud"
test -x "\$GCLOUD_FINAL"
FINAL_VERSION_CONFIG="\$(mktemp -d "\$INSTALL_ROOT/.version_config.XXXXXX")"
GCLOUD_VERSION_JSON="\$(CLOUDSDK_CONFIG="\$FINAL_VERSION_CONFIG" "\$GCLOUD_FINAL" version --format=json 2>/dev/null)"
GCLOUD_VERSION="\$(printf '%s' "\$GCLOUD_VERSION_JSON" | "\$PINNED_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["Google Cloud SDK"])')"
unset GCLOUD_VERSION_JSON
test "\$GCLOUD_VERSION" = "\$VERSION"
printf '%s\n' 'gcloud_bootstrap_status=PASS_SELF_CONTAINED_PINNED_INSTALL'
printf '%s\n' "gcloud_bootstrap_executable=\$GCLOUD_FINAL"
EOF
chmod 600 "$temporary_script"
mv "$temporary_script" "$output_script"
chmod 600 "$output_script"

printf '%s\n' 'gcloud_bootstrap_prepare_status=PASS_PREPARED_NOT_EXECUTED'
printf 'gcloud_bootstrap_prepare_output=%s\n' "$output_script"
