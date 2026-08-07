# SCC Phase 1E-B/C metadata-only commands

Status: **authorized for filesystem/quota inventory, GCS metadata reconciliation, technical metadata review, and clinician-packet preparation only**. These commands do not call a GCS media endpoint, download DICOM bodies, extract cines, create embeddings, fit models, generate predictions, or read confirmatory performance.

Run each block as a separate subprocess. Block 1 creates a mode-600, owner-only session-state file at `/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env`; every later block validates and sources that file before using prior state. The session file contains paths, checksums, and run identity but never the requester-pays billing-project value. Detailed logs and row-level outputs remain under `/restricted/projectnb/mimicecho`; do not copy them into Git.

## 1. Refresh and verify the dedicated SCC worktree

```bash
set -euo pipefail
WORKTREE="/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
cd "$WORKTREE"
git fetch origin --prune
git switch codex/lvef-multitask-revalidation
git pull --ff-only origin codex/lvef-multitask-revalidation
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test -z "$(git status --porcelain)"
EXPECTED_COMMIT="$(git rev-parse HEAD)"

PYTHON_RECORD_PARENT="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_bootstrap"
mkdir -p "$PYTHON_RECORD_PARENT"
umask 077
PYTHON_RECORD="$PYTHON_RECORD_PARENT/lvef_scc_python_resolution_$(date -u +%Y%m%dT%H%M%SZ)_$$.json"
test ! -e "$PYTHON_RECORD"
export LVEF_SCC_PYTHON="/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python"
PYTHON="$(scripts/resolve_lvef_scc_python.sh --record-json "$PYTHON_RECORD")"
test -x "$PYTHON"
EXPECTED_PYTHON_SHA256="1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"
test "$(sha256sum "$PYTHON" | awk '{print $1}')" = "$EXPECTED_PYTHON_SHA256"
test -f "$PYTHON_RECORD"
test "$(stat -c '%a' "$PYTHON_RECORD")" = "600"

SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
mkdir -p "$(dirname "$SESSION_ENV")"
SESSION_ENV_NEXT="$(mktemp "$SESSION_ENV.tmp.XXXXXX")"
chmod 600 "$SESSION_ENV_NEXT"
{
  printf 'WORKTREE=%q\n' "$WORKTREE"
  printf 'EXPECTED_COMMIT=%q\n' "$EXPECTED_COMMIT"
  printf 'PYTHON=%q\n' "$PYTHON"
  printf 'PYTHON_RECORD=%q\n' "$PYTHON_RECORD"
  printf 'EXPECTED_PYTHON_SHA256=%q\n' "$EXPECTED_PYTHON_SHA256"
} >"$SESSION_ENV_NEXT"
mv -f "$SESSION_ENV_NEXT" "$SESSION_ENV"
chmod 600 "$SESSION_ENV"
```

## 2. Record quota and filesystem witnesses

The quota display may round usage. Preserve it as the allocation witness, and use `du -x -B1` as the exact allocated-byte input for the current project root.

```bash
set -euo pipefail
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
PREFLIGHT_PARENT="/restricted/projectnb/mimicecho/audits"
RUN_ID="lvef_multitask_phase1ebc_$(date -u +%Y%m%dT%H%M%SZ)_$(git rev-parse --short=12 HEAD)"
RUN_ROOT="$PREFLIGHT_PARENT/$RUN_ID"
mkdir -p "$RUN_ROOT/restricted/quota" "$RUN_ROOT/restricted/logs" "$RUN_ROOT/aggregate" "$RUN_ROOT/scheduler_logs"
umask 077
pquota -u mimicecho >"$RUN_ROOT/restricted/quota/pquota_before.txt" 2>"$RUN_ROOT/restricted/quota/pquota_before.stderr.txt"
df -P /restricted/project/mimicecho /restricted/projectnb/mimicecho >"$RUN_ROOT/restricted/quota/df_before.txt"
findmnt --target /restricted/project/mimicecho >"$RUN_ROOT/restricted/quota/findmnt_project.txt"
findmnt --target /restricted/projectnb/mimicecho >"$RUN_ROOT/restricted/quota/findmnt_projectnb.txt"
CURRENT_RESEARCH_USAGE_BYTES="$(du -sx -B1 /restricted/projectnb/mimicecho | awk 'NR==1 {print $1}')"
[[ "$CURRENT_RESEARCH_USAGE_BYTES" =~ ^[0-9]+$ ]]
RESOURCE_POLICY="$WORKTREE/configs/lvef_c3_resource_policy.yaml"
SAFE_EXPORT_POLICY="$WORKTREE/configs/lvef_multitask_safe_export_policy.yaml"
STORAGE_DETAIL="$RUN_ROOT/restricted/scc_storage_inventory.restricted.json"
STORAGE_SUMMARY="$RUN_ROOT/aggregate/scc_storage_inventory.summary.json"
if [[ -e "$STORAGE_DETAIL" || -e "$STORAGE_SUMMARY" ]]; then
  test -f "$STORAGE_DETAIL"
  test -f "$STORAGE_SUMMARY"
else
  "$PYTHON" scripts/audit_lvef_c3_storage.py \
    --resource-policy "$RESOURCE_POLICY" \
    --safe-export-policy "$SAFE_EXPORT_POLICY" \
    --restricted-output "$STORAGE_DETAIL" \
    --aggregate-output "$STORAGE_SUMMARY" \
    >"$RUN_ROOT/restricted/logs/storage_audit.stdout.txt" \
    2>"$RUN_ROOT/restricted/logs/storage_audit.stderr.txt"
fi
"$PYTHON" scripts/validate_lvef_c3_resource_preflight_outputs.py \
  --stage storage --run-root "$RUN_ROOT" >/dev/null

MIGRATION_CLASSIFICATION="$RUN_ROOT/restricted/quota/disaster_tier_path_classification.restricted.json"
MIGRATION_WITNESS="$RUN_ROOT/restricted/quota/classified_migration_witness.json"
"$PYTHON" scripts/build_lvef_c3_migration_witness.py \
  --storage-detail "$STORAGE_DETAIL" \
  --classification-output "$MIGRATION_CLASSIFICATION" \
  --witness-output "$MIGRATION_WITNESS" \
  --safe-export-policy "$SAFE_EXPORT_POLICY" \
  --planning-mode FULL_MIGRATION_AFTER_BACKUP \
  >"$RUN_ROOT/restricted/logs/migration_witness.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/migration_witness.stderr.txt"
test -f "$MIGRATION_CLASSIFICATION"
test -f "$MIGRATION_WITNESS"
EXPECTED_RESOURCE_POLICY_SHA256="$(sha256sum "$RESOURCE_POLICY" | awk '{print $1}')"
EXPECTED_SAFE_EXPORT_POLICY_SHA256="$(sha256sum "$SAFE_EXPORT_POLICY" | awk '{print $1}')"
EXPECTED_MIGRATION_CLASSIFICATION_SHA256="$(sha256sum "$MIGRATION_CLASSIFICATION" | awk '{print $1}')"
EXPECTED_MIGRATION_WITNESS_SHA256="$(sha256sum "$MIGRATION_WITNESS" | awk '{print $1}')"
SESSION_ENV_NEXT="$(mktemp "$SESSION_ENV.tmp.XXXXXX")"
chmod 600 "$SESSION_ENV_NEXT"
cp "$SESSION_ENV" "$SESSION_ENV_NEXT"
{
  printf 'PREFLIGHT_PARENT=%q\n' "$PREFLIGHT_PARENT"
  printf 'RUN_ID=%q\n' "$RUN_ID"
  printf 'RUN_ROOT=%q\n' "$RUN_ROOT"
  printf 'CURRENT_RESEARCH_USAGE_BYTES=%q\n' "$CURRENT_RESEARCH_USAGE_BYTES"
  printf 'RESOURCE_POLICY=%q\n' "$RESOURCE_POLICY"
  printf 'SAFE_EXPORT_POLICY=%q\n' "$SAFE_EXPORT_POLICY"
  printf 'STORAGE_DETAIL=%q\n' "$STORAGE_DETAIL"
  printf 'STORAGE_SUMMARY=%q\n' "$STORAGE_SUMMARY"
  printf 'MIGRATION_CLASSIFICATION=%q\n' "$MIGRATION_CLASSIFICATION"
  printf 'MIGRATION_WITNESS=%q\n' "$MIGRATION_WITNESS"
  printf 'EXPECTED_RESOURCE_POLICY_SHA256=%q\n' "$EXPECTED_RESOURCE_POLICY_SHA256"
  printf 'EXPECTED_SAFE_EXPORT_POLICY_SHA256=%q\n' "$EXPECTED_SAFE_EXPORT_POLICY_SHA256"
  printf 'EXPECTED_MIGRATION_CLASSIFICATION_SHA256=%q\n' "$EXPECTED_MIGRATION_CLASSIFICATION_SHA256"
  printf 'EXPECTED_MIGRATION_WITNESS_SHA256=%q\n' "$EXPECTED_MIGRATION_WITNESS_SHA256"
} >>"$SESSION_ENV_NEXT"
mv -f "$SESSION_ENV_NEXT" "$SESSION_ENV"
chmod 600 "$SESSION_ENV"
```

Block 2 creates only a conservative planning witness. It recursively inventories symlink objects on the root filesystem without following their targets and separately inventories nested mount points. An existing internal symlink may remain in the migration plan only when its target exists inside the same top-level disaster-tier scope; the plan requires preserving the link object and internal target after a separately verified backup. The complete top-level scope containing a dangling, cyclic, external, or cross-scope link is retained pending explicit symlink adjudication. Uncovered or malformed links and every nested mount fail closed. Exact paths and targets remain restricted. Migrated plus retained bytes must reconcile to the complete inventory. The witness records `PLANNED_NOT_EXECUTED` and explicitly records that neither backup nor migration was completed. The restricted path-level classification and its witness are checksum-bound to the current storage-detail JSON. The 50-GB retention option remains provisional and is not used as an authority.

## 3. Build the mode-600 GCS/resource preflight environment

The selected-source checksum is read from the passed Phase 1E-A manifest authority and independently compared with the file bytes. The requester-pays project is entered only into the restricted environment file.

```bash
set -euo pipefail
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
FULLSCALE_ROOT="/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all"
SMOKE_AUTH_ROOT="/restricted/project/mimicecho/audits/lvef_multitask_phase1e_a_20260804T125829Z_022d7581eee4"
SELECTED_STUDIES="$FULLSCALE_ROOT/manifests/all_eligible_studies.csv"
SPLIT_MAP="$FULLSCALE_ROOT/manifests/subject_split_map_v1.csv"
SELECTED_SOURCE_MANIFEST="$SMOKE_AUTH_ROOT/restricted/source/selected_source_manifest_restricted.csv"
SOURCE_AUTHORITY_SUMMARY="$SMOKE_AUTH_ROOT/aggregate/source/reconstruction_source_manifest.summary.json"
test -f "$RESOURCE_POLICY"
test -f "$SAFE_EXPORT_POLICY"
test -f "$MIGRATION_CLASSIFICATION"
test -f "$MIGRATION_WITNESS"
test "$(sha256sum "$RESOURCE_POLICY" | awk '{print $1}')" = "$EXPECTED_RESOURCE_POLICY_SHA256"
test "$(sha256sum "$SAFE_EXPORT_POLICY" | awk '{print $1}')" = "$EXPECTED_SAFE_EXPORT_POLICY_SHA256"
test "$(sha256sum "$MIGRATION_CLASSIFICATION" | awk '{print $1}')" = "$EXPECTED_MIGRATION_CLASSIFICATION_SHA256"
test "$(sha256sum "$MIGRATION_WITNESS" | awk '{print $1}')" = "$EXPECTED_MIGRATION_WITNESS_SHA256"

EXPECTED_SELECTED_SOURCE_SHA256="$(
  "$PYTHON" -c '
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
value = payload["selected_source_manifest_sha256"]
assert isinstance(value, str) and len(value) == 64
print(value)
' "$SOURCE_AUTHORITY_SUMMARY"
)"
test "$(sha256sum "$SELECTED_SOURCE_MANIFEST" | awk '{print $1}')" = "$EXPECTED_SELECTED_SOURCE_SHA256"
EXPECTED_SELECTED_STUDIES_SHA256="920aa8742297dd90c5f125723a425a85201fa7966e926b3191f2c4a57b3d31c1"
EXPECTED_SPLIT_MAP_SHA256="c5101cea1d76b38c6bb4517edf4b463b338d7505032cfa40bc8f27ca5b97e517"
test "$(sha256sum "$SELECTED_STUDIES" | awk '{print $1}')" = "$EXPECTED_SELECTED_STUDIES_SHA256"
test "$(sha256sum "$SPLIT_MAP" | awk '{print $1}')" = "$EXPECTED_SPLIT_MAP_SHA256"
read -r -s -p 'Approved requester-pays GCP project: ' LVEF_C3_GCP_BILLING_PROJECT
printf '\n'
test -n "$LVEF_C3_GCP_BILLING_PROJECT"
PREFLIGHT_ENV="$RUN_ROOT/restricted/lvef_c3_preflight.env"
install -m 600 /dev/null "$PREFLIGHT_ENV"
{
  printf 'WORKTREE=%q\n' "$WORKTREE"
  printf 'EXPECTED_COMMIT=%q\n' "$EXPECTED_COMMIT"
  printf 'PYTHON=%q\n' "$PYTHON"
  printf 'EXPECTED_PYTHON_SHA256=%q\n' "$EXPECTED_PYTHON_SHA256"
  printf 'SELECTED_STUDIES=%q\n' "$SELECTED_STUDIES"
  printf 'SPLIT_MAP=%q\n' "$SPLIT_MAP"
  printf 'SELECTED_SOURCE_MANIFEST=%q\n' "$SELECTED_SOURCE_MANIFEST"
  printf 'EXPECTED_SELECTED_SOURCE_SHA256=%q\n' "$EXPECTED_SELECTED_SOURCE_SHA256"
  printf 'RESOURCE_POLICY=%q\n' "$RESOURCE_POLICY"
  printf 'SAFE_EXPORT_POLICY=%q\n' "$SAFE_EXPORT_POLICY"
  printf 'EXPECTED_SELECTED_STUDIES_SHA256=%q\n' "$EXPECTED_SELECTED_STUDIES_SHA256"
  printf 'EXPECTED_SPLIT_MAP_SHA256=%q\n' "$EXPECTED_SPLIT_MAP_SHA256"
  printf 'EXPECTED_RESOURCE_POLICY_SHA256=%q\n' "$EXPECTED_RESOURCE_POLICY_SHA256"
  printf 'EXPECTED_SAFE_EXPORT_POLICY_SHA256=%q\n' "$EXPECTED_SAFE_EXPORT_POLICY_SHA256"
  printf 'CURRENT_RESEARCH_USAGE_BYTES=%q\n' "$CURRENT_RESEARCH_USAGE_BYTES"
  printf 'MIGRATION_WITNESS=%q\n' "$MIGRATION_WITNESS"
  printf 'EXPECTED_MIGRATION_WITNESS_SHA256=%q\n' "$EXPECTED_MIGRATION_WITNESS_SHA256"
  printf 'APPROVED_ORGANIZATION_CLASS=%q\n' 'institutionally_managed_enterprise_edu_healthcare_or_api_organization'
  printf 'RUN_ROOT=%q\n' "$RUN_ROOT"
  printf 'LVEF_C3_GCP_BILLING_PROJECT=%q\n' "$LVEF_C3_GCP_BILLING_PROJECT"
} >"$PREFLIGHT_ENV"
chmod 600 "$PREFLIGHT_ENV"
unset LVEF_C3_GCP_BILLING_PROJECT
SESSION_ENV_NEXT="$(mktemp "$SESSION_ENV.tmp.XXXXXX")"
chmod 600 "$SESSION_ENV_NEXT"
cp "$SESSION_ENV" "$SESSION_ENV_NEXT"
{
  printf 'FULLSCALE_ROOT=%q\n' "$FULLSCALE_ROOT"
  printf 'SMOKE_AUTH_ROOT=%q\n' "$SMOKE_AUTH_ROOT"
  printf 'SELECTED_STUDIES=%q\n' "$SELECTED_STUDIES"
  printf 'SPLIT_MAP=%q\n' "$SPLIT_MAP"
  printf 'SELECTED_SOURCE_MANIFEST=%q\n' "$SELECTED_SOURCE_MANIFEST"
  printf 'SOURCE_AUTHORITY_SUMMARY=%q\n' "$SOURCE_AUTHORITY_SUMMARY"
  printf 'RESOURCE_POLICY=%q\n' "$RESOURCE_POLICY"
  printf 'SAFE_EXPORT_POLICY=%q\n' "$SAFE_EXPORT_POLICY"
  printf 'EXPECTED_SELECTED_SOURCE_SHA256=%q\n' "$EXPECTED_SELECTED_SOURCE_SHA256"
  printf 'EXPECTED_SELECTED_STUDIES_SHA256=%q\n' "$EXPECTED_SELECTED_STUDIES_SHA256"
  printf 'EXPECTED_SPLIT_MAP_SHA256=%q\n' "$EXPECTED_SPLIT_MAP_SHA256"
  printf 'EXPECTED_RESOURCE_POLICY_SHA256=%q\n' "$EXPECTED_RESOURCE_POLICY_SHA256"
  printf 'EXPECTED_SAFE_EXPORT_POLICY_SHA256=%q\n' "$EXPECTED_SAFE_EXPORT_POLICY_SHA256"
  printf 'MIGRATION_WITNESS=%q\n' "$MIGRATION_WITNESS"
  printf 'EXPECTED_MIGRATION_WITNESS_SHA256=%q\n' "$EXPECTED_MIGRATION_WITNESS_SHA256"
  printf 'PREFLIGHT_ENV=%q\n' "$PREFLIGHT_ENV"
} >>"$SESSION_ENV_NEXT"
mv -f "$SESSION_ENV_NEXT" "$SESSION_ENV"
chmod 600 "$SESSION_ENV"
```

## 3A. Repair the preserved run authority after the portability commit

Run this block once **after** the portability repair has been fast-forwarded into
the dedicated SCC worktree. It does not rerun Block 2 and does not create a new
run root. It accepts only the exact previously bound commit, requires the new
commit to be the synchronized remote tip and a fast-forward descendant, verifies
the unchanged resource/migration/input checksums, migrates the safe-export-policy
authority from one exact prior checksum to one exact new checksum in both
owner-only environment files, performs the same exact migration for the changed
GCP authority wrapper, atomically replaces the `EXPECTED_COMMIT` assignment,
and adds the already established pinned-Python checksum when absent.
The existing requester-pays value is copied byte-for-byte and is never printed.

```bash
set -euo pipefail
umask 077
WORKTREE="/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test ! -L "$SESSION_ENV"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
case "$EXPECTED_COMMIT" in
  177aac1ce498390f62d43fb76ca216d06dc6b25f|20d847648406d0a556957e0f5bc25dde392f8244|6845bd180ee5811151929920234f53f15b272b14)
    PRIOR_EXPECTED_COMMIT="$EXPECTED_COMMIT"
    ;;
  *)
    printf '%s\n' 'PHASE1EBC_EXISTING_RUN_AUTHORITY_REPAIR=BLOCKED_UNTRUSTED_PRIOR_COMMIT' >&2
    exit 65
    ;;
esac
EXPECTED_PYTHON_SHA256="1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"
PRIOR_SAFE_EXPORT_POLICY_SHA256="b4ba5df3ff5265375868b3ffcb3b3ceaa8fd768f0f65e0d27b45e751e457046b"
NEW_SAFE_EXPORT_POLICY_SHA256="76ad8e0673036b321a755d537d52fc7627f81eab07fe7be78f3ce31b3f5bb110"
PRIOR_GCP_AUTHORITY_WRAPPER_SHA256="8e2d0dc213007c0f8d789cb0fbf3aee3e8dcf9892c7b6774b02e9ec18354f365"
NEW_GCP_AUTHORITY_WRAPPER_SHA256="9a7053b568b16ab00e4969d73a1db76c7de85efa4b7a955445ca4aeea6af7a12"
test "$(sha256sum "$PYTHON" | awk '{print $1}')" = "$EXPECTED_PYTHON_SHA256"
test -d "$RUN_ROOT"
test ! -L "$PREFLIGHT_ENV"
test -f "$PREFLIGHT_ENV"
test -O "$PREFLIGHT_ENV"
test "$(stat -c '%a' "$PREFLIGHT_ENV")" = "600"
test "$(grep -c '^EXPECTED_COMMIT=' "$SESSION_ENV")" -eq 1
test "$(grep -c '^EXPECTED_COMMIT=' "$PREFLIGHT_ENV")" -eq 1
test "$(grep '^EXPECTED_COMMIT=' "$PREFLIGHT_ENV")" = "EXPECTED_COMMIT=$PRIOR_EXPECTED_COMMIT"
test "$(grep -c '^LVEF_C3_GCP_BILLING_PROJECT=' "$PREFLIGHT_ENV")" -eq 1
test "$EXPECTED_SAFE_EXPORT_POLICY_SHA256" = "$PRIOR_SAFE_EXPORT_POLICY_SHA256"
for AUTHORITY_FILE in "$SESSION_ENV" "$PREFLIGHT_ENV"; do
  test "$(grep -c '^EXPECTED_SAFE_EXPORT_POLICY_SHA256=' "$AUTHORITY_FILE")" -eq 1
  test "$(grep '^EXPECTED_SAFE_EXPORT_POLICY_SHA256=' "$AUTHORITY_FILE")" = \
    "EXPECTED_SAFE_EXPORT_POLICY_SHA256=$PRIOR_SAFE_EXPORT_POLICY_SHA256"
  test "$(grep -c '^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=' "$AUTHORITY_FILE")" -eq 1
  test "$(grep '^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=' "$AUTHORITY_FILE")" = \
    "EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=$PRIOR_GCP_AUTHORITY_WRAPPER_SHA256"
done

cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test -z "$(git status --porcelain)"
git fetch origin --prune
NEW_EXPECTED_COMMIT="$(git rev-parse HEAD)"
test "$NEW_EXPECTED_COMMIT" = "$(git rev-parse origin/codex/lvef-multitask-revalidation)"
test "$NEW_EXPECTED_COMMIT" != "$PRIOR_EXPECTED_COMMIT"
git merge-base --is-ancestor "$PRIOR_EXPECTED_COMMIT" "$NEW_EXPECTED_COMMIT"

test "$(sha256sum "$RESOURCE_POLICY" | awk '{print $1}')" = "$EXPECTED_RESOURCE_POLICY_SHA256"
test "$(sha256sum "$SAFE_EXPORT_POLICY" | awk '{print $1}')" = "$NEW_SAFE_EXPORT_POLICY_SHA256"
test "$(sha256sum "$GCP_AUTHORITY_WRAPPER" | awk '{print $1}')" = "$NEW_GCP_AUTHORITY_WRAPPER_SHA256"
test "$(sha256sum "$MIGRATION_CLASSIFICATION" | awk '{print $1}')" = "$EXPECTED_MIGRATION_CLASSIFICATION_SHA256"
test "$(sha256sum "$MIGRATION_WITNESS" | awk '{print $1}')" = "$EXPECTED_MIGRATION_WITNESS_SHA256"
test "$(sha256sum "$SELECTED_SOURCE_MANIFEST" | awk '{print $1}')" = "$EXPECTED_SELECTED_SOURCE_SHA256"
test "$(sha256sum "$SELECTED_STUDIES" | awk '{print $1}')" = "$EXPECTED_SELECTED_STUDIES_SHA256"
test "$(sha256sum "$SPLIT_MAP" | awk '{print $1}')" = "$EXPECTED_SPLIT_MAP_SHA256"

migrate_safe_export_policy_checksum() {
  local authority_file="$1"
  local temporary_file
  test -f "$authority_file"
  test -O "$authority_file"
  test "$(stat -c '%a' "$authority_file")" = "600"
  test "$(grep -c '^EXPECTED_SAFE_EXPORT_POLICY_SHA256=' "$authority_file")" -eq 1
  test "$(grep '^EXPECTED_SAFE_EXPORT_POLICY_SHA256=' "$authority_file")" = \
    "EXPECTED_SAFE_EXPORT_POLICY_SHA256=$PRIOR_SAFE_EXPORT_POLICY_SHA256"
  temporary_file="$(mktemp "${authority_file}.tmp.XXXXXX")"
  chmod 600 "$temporary_file"
  awk -v replacement="EXPECTED_SAFE_EXPORT_POLICY_SHA256=$NEW_SAFE_EXPORT_POLICY_SHA256" '
    BEGIN { replaced = 0 }
    /^EXPECTED_SAFE_EXPORT_POLICY_SHA256=/ {
      if (replaced != 0) exit 74
      print replacement
      replaced = 1
      next
    }
    { print }
    END { if (replaced != 1) exit 74 }
  ' "$authority_file" >"$temporary_file"
  test "$(grep -c '^EXPECTED_SAFE_EXPORT_POLICY_SHA256=' "$temporary_file")" -eq 1
  test "$(grep '^EXPECTED_SAFE_EXPORT_POLICY_SHA256=' "$temporary_file")" = \
    "EXPECTED_SAFE_EXPORT_POLICY_SHA256=$NEW_SAFE_EXPORT_POLICY_SHA256"
  mv -f "$temporary_file" "$authority_file"
  chmod 600 "$authority_file"
}

migrate_safe_export_policy_checksum "$SESSION_ENV"
migrate_safe_export_policy_checksum "$PREFLIGHT_ENV"
EXPECTED_SAFE_EXPORT_POLICY_SHA256="$NEW_SAFE_EXPORT_POLICY_SHA256"
test "$(grep '^EXPECTED_SAFE_EXPORT_POLICY_SHA256=' "$SESSION_ENV")" = \
  "EXPECTED_SAFE_EXPORT_POLICY_SHA256=$NEW_SAFE_EXPORT_POLICY_SHA256"
test "$(grep '^EXPECTED_SAFE_EXPORT_POLICY_SHA256=' "$PREFLIGHT_ENV")" = \
  "EXPECTED_SAFE_EXPORT_POLICY_SHA256=$NEW_SAFE_EXPORT_POLICY_SHA256"

migrate_gcp_authority_wrapper_checksum() {
  local authority_file="$1"
  local temporary_file
  test -f "$authority_file"
  test -O "$authority_file"
  test "$(stat -c '%a' "$authority_file")" = "600"
  test "$(grep -c '^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=' "$authority_file")" -eq 1
  test "$(grep '^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=' "$authority_file")" = \
    "EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=$PRIOR_GCP_AUTHORITY_WRAPPER_SHA256"
  temporary_file="$(mktemp "${authority_file}.tmp.XXXXXX")"
  chmod 600 "$temporary_file"
  awk -v replacement="EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=$NEW_GCP_AUTHORITY_WRAPPER_SHA256" '
    BEGIN { replaced = 0 }
    /^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=/ {
      if (replaced != 0) exit 74
      print replacement
      replaced = 1
      next
    }
    { print }
    END { if (replaced != 1) exit 74 }
  ' "$authority_file" >"$temporary_file"
  test "$(grep -c '^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=' "$temporary_file")" -eq 1
  test "$(grep '^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=' "$temporary_file")" = \
    "EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=$NEW_GCP_AUTHORITY_WRAPPER_SHA256"
  mv -f "$temporary_file" "$authority_file"
  chmod 600 "$authority_file"
}

migrate_gcp_authority_wrapper_checksum "$SESSION_ENV"
migrate_gcp_authority_wrapper_checksum "$PREFLIGHT_ENV"
EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256="$NEW_GCP_AUTHORITY_WRAPPER_SHA256"
test "$(grep '^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=' "$SESSION_ENV")" = \
  "EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=$NEW_GCP_AUTHORITY_WRAPPER_SHA256"
test "$(grep '^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=' "$PREFLIGHT_ENV")" = \
  "EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=$NEW_GCP_AUTHORITY_WRAPPER_SHA256"

repair_expected_commit() {
  local authority_file="$1"
  local temporary_file
  temporary_file="$(mktemp "${authority_file}.tmp.XXXXXX")"
  chmod 600 "$temporary_file"
  awk -v replacement="EXPECTED_COMMIT=$NEW_EXPECTED_COMMIT" '
    BEGIN { replaced = 0 }
    /^EXPECTED_COMMIT=/ {
      if (replaced != 0) exit 74
      print replacement
      replaced = 1
      next
    }
    { print }
    END { if (replaced != 1) exit 74 }
  ' "$authority_file" >"$temporary_file"
  test "$(grep -c '^EXPECTED_COMMIT=' "$temporary_file")" -eq 1
  mv -f "$temporary_file" "$authority_file"
  chmod 600 "$authority_file"
}

repair_expected_commit "$SESSION_ENV"
repair_expected_commit "$PREFLIGHT_ENV"

append_python_authority_if_absent() {
  local authority_file="$1"
  local temporary_file
  if grep -q '^EXPECTED_PYTHON_SHA256=' "$authority_file"; then
    test "$(grep '^EXPECTED_PYTHON_SHA256=' "$authority_file")" = \
      "EXPECTED_PYTHON_SHA256=$EXPECTED_PYTHON_SHA256"
    return
  fi
  temporary_file="$(mktemp "${authority_file}.tmp.XXXXXX")"
  chmod 600 "$temporary_file"
  cp "$authority_file" "$temporary_file"
  printf 'EXPECTED_PYTHON_SHA256=%q\n' "$EXPECTED_PYTHON_SHA256" >>"$temporary_file"
  mv -f "$temporary_file" "$authority_file"
  chmod 600 "$authority_file"
}
append_python_authority_if_absent "$SESSION_ENV"
append_python_authority_if_absent "$PREFLIGHT_ENV"
test "$(grep '^EXPECTED_COMMIT=' "$SESSION_ENV")" = "EXPECTED_COMMIT=$NEW_EXPECTED_COMMIT"
test "$(grep '^EXPECTED_COMMIT=' "$PREFLIGHT_ENV")" = "EXPECTED_COMMIT=$NEW_EXPECTED_COMMIT"
test "$RUN_ROOT" = "$(. "$SESSION_ENV"; printf '%s' "$RUN_ROOT")"
printf '%s\n' 'PHASE1EBC_EXISTING_RUN_AUTHORITY_REPAIR=PASS_NO_STORAGE_RERUN'
```

## 3B. Resolve Google Cloud CLI or prepare a pinned bootstrap

This block first prefers an owner-specified absolute `LVEF_SCC_GCLOUD`, then an
existing `gcloud` on `PATH`, then known self-contained user/project installs,
and finally the pinned SCC environment module. Resolution reads no credential
material and writes a restricted provenance record. If no valid executable is
found, it prepares—but does not execute—a mode-600 bootstrap script for Google
Cloud CLI 579.0.0 from the exact official versioned archive, pinned to SHA-256
`a9a7fbe51cda37cf6142b1bbcff12227550e60a6c67e8cf84644fb301371c4de`
(96,066,973 bytes; decompressed tar-payload SHA-256
`f44705777ec8b5b401ff705c39421f747780b7fb7655f836af43e316964b90bd`).
The prepared bootstrap targets only `/restricted/projectnb/mimicecho/tools`,
does not modify profiles or system packages, and requires separate owner review
and authorization before it may be executed.

```bash
set -euo pipefail
umask 077
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test ! -L "$SESSION_ENV"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test "$(git rev-parse origin/codex/lvef-multitask-revalidation)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"

GCLOUD_RESOLUTION_RECORD="$RUN_ROOT/restricted/lvef_scc_gcloud_resolution_$(date -u +%Y%m%dT%H%M%SZ)_$$.json"
GCLOUD_RESOLVER="$WORKTREE/scripts/resolve_lvef_scc_gcloud.sh"
test -f "$GCLOUD_RESOLVER"
EXPECTED_GCLOUD_RESOLVER_SHA256="$(sha256sum "$GCLOUD_RESOLVER" | awk '{print $1}')"
[[ "$EXPECTED_GCLOUD_RESOLVER_SHA256" =~ ^[0-9a-f]{64}$ ]]
test ! -e "$GCLOUD_RESOLUTION_RECORD"
if GCLOUD="$(
  LVEF_SCC_PYTHON="$PYTHON" "$GCLOUD_RESOLVER" \
    --record-json "$GCLOUD_RESOLUTION_RECORD" \
    --expected-version 579.0.0
)"; then
  test -x "$GCLOUD"
  test -f "$GCLOUD_RESOLUTION_RECORD"
  test "$(stat -c '%a' "$GCLOUD_RESOLUTION_RECORD")" = "600"
else
  RESOLUTION_STATUS=$?
  BOOTSTRAP_REVIEW_SCRIPT="$RUN_ROOT/restricted/google_cloud_cli_579_0_0_bootstrap.PREPARED_NOT_EXECUTED.sh"
  test ! -e "$BOOTSTRAP_REVIEW_SCRIPT"
  scripts/prepare_lvef_scc_gcloud_cli_bootstrap.sh \
    --output-script "$BOOTSTRAP_REVIEW_SCRIPT"
  test "$(stat -c '%a' "$BOOTSTRAP_REVIEW_SCRIPT")" = "600"
  printf '%s\n' 'GCLOUD_RESOLUTION=BLOCKED_BOOTSTRAP_PREPARED_NOT_EXECUTED' >&2
  exit "$RESOLUTION_STATUS"
fi
EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256="$(sha256sum "$GCLOUD_RESOLUTION_RECORD" | awk '{print $1}')"
[[ "$EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256" =~ ^[0-9a-f]{64}$ ]]
GCP_AUTHORITY_WRAPPER="$WORKTREE/scripts/verify_lvef_scc_gcp_authority.sh"
C3_EXECUTION_CONTRACT="$WORKTREE/configs/lvef_c3_execution_contract.yaml"
test -f "$GCP_AUTHORITY_WRAPPER"
test -x "$GCP_AUTHORITY_WRAPPER"
test -f "$C3_EXECUTION_CONTRACT"
EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256="$(sha256sum "$GCP_AUTHORITY_WRAPPER" | awk '{print $1}')"
EXPECTED_C3_EXECUTION_CONTRACT_SHA256="$(sha256sum "$C3_EXECUTION_CONTRACT" | awk '{print $1}')"
[[ "$EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256" =~ ^[0-9a-f]{64}$ ]]
[[ "$EXPECTED_C3_EXECUTION_CONTRACT_SHA256" =~ ^[0-9a-f]{64}$ ]]
CLOUDSDK_CONFIG="$RUN_ROOT/restricted/gcloud_config"
if [[ -e "$CLOUDSDK_CONFIG" ]]; then
  "$WORKTREE/scripts/check_lvef_private_directory.sh" "$CLOUDSDK_CONFIG"
  CLOUDSDK_CONFIG_FIRST_ENTRY="$(find "$CLOUDSDK_CONFIG" -mindepth 1 -print -quit)"
  test -z "$CLOUDSDK_CONFIG_FIRST_ENTRY"
else
  mkdir "$CLOUDSDK_CONFIG"
  chmod 700 "$CLOUDSDK_CONFIG"
  "$WORKTREE/scripts/check_lvef_private_directory.sh" "$CLOUDSDK_CONFIG"
fi

test "$(grep -c '^GCLOUD=' "$SESSION_ENV")" -eq 0
SESSION_ENV_NEXT="$(mktemp "$SESSION_ENV.tmp.XXXXXX")"
chmod 600 "$SESSION_ENV_NEXT"
cp "$SESSION_ENV" "$SESSION_ENV_NEXT"
{
  printf 'GCLOUD=%q\n' "$GCLOUD"
  printf 'GCLOUD_RESOLVER=%q\n' "$GCLOUD_RESOLVER"
  printf 'EXPECTED_GCLOUD_RESOLVER_SHA256=%q\n' "$EXPECTED_GCLOUD_RESOLVER_SHA256"
  printf 'GCLOUD_RESOLUTION_RECORD=%q\n' "$GCLOUD_RESOLUTION_RECORD"
  printf 'EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256=%q\n' "$EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256"
  printf 'GCP_AUTHORITY_WRAPPER=%q\n' "$GCP_AUTHORITY_WRAPPER"
  printf 'EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=%q\n' "$EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256"
  printf 'C3_EXECUTION_CONTRACT=%q\n' "$C3_EXECUTION_CONTRACT"
  printf 'EXPECTED_C3_EXECUTION_CONTRACT_SHA256=%q\n' "$EXPECTED_C3_EXECUTION_CONTRACT_SHA256"
  printf 'CLOUDSDK_CONFIG=%q\n' "$CLOUDSDK_CONFIG"
} >>"$SESSION_ENV_NEXT"
mv -f "$SESSION_ENV_NEXT" "$SESSION_ENV"
chmod 600 "$SESSION_ENV"

read -r -s -p 'Approved active Google identity: ' LVEF_C3_EXPECTED_GCP_ACCOUNT
printf '\n'
test -n "$LVEF_C3_EXPECTED_GCP_ACCOUNT"
read -r -s -p 'Approved Google Cloud project display name: ' LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME
printf '\n'
test -n "$LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME"
LVEF_C3_GCP_AUTHORIZED_USER_FILE=""
test "$(grep -c '^GCLOUD=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^GCLOUD_RESOLVER=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^EXPECTED_GCLOUD_RESOLVER_SHA256=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^LVEF_C3_EXPECTED_GCP_ACCOUNT=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^LVEF_C3_GCP_AUTHORIZED_USER_FILE=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^GCLOUD_RESOLUTION_RECORD=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^GCP_AUTHORITY_WRAPPER=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^C3_EXECUTION_CONTRACT=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^EXPECTED_C3_EXECUTION_CONTRACT_SHA256=' "$PREFLIGHT_ENV")" -eq 0
test "$(grep -c '^CLOUDSDK_CONFIG=' "$PREFLIGHT_ENV")" -eq 0
PREFLIGHT_ENV_NEXT="$(mktemp "$PREFLIGHT_ENV.tmp.XXXXXX")"
chmod 600 "$PREFLIGHT_ENV_NEXT"
cp "$PREFLIGHT_ENV" "$PREFLIGHT_ENV_NEXT"
{
  printf 'GCLOUD=%q\n' "$GCLOUD"
  printf 'GCLOUD_RESOLVER=%q\n' "$GCLOUD_RESOLVER"
  printf 'EXPECTED_GCLOUD_RESOLVER_SHA256=%q\n' "$EXPECTED_GCLOUD_RESOLVER_SHA256"
  printf 'GCLOUD_RESOLUTION_RECORD=%q\n' "$GCLOUD_RESOLUTION_RECORD"
  printf 'EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256=%q\n' "$EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256"
  printf 'GCP_AUTHORITY_WRAPPER=%q\n' "$GCP_AUTHORITY_WRAPPER"
  printf 'EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256=%q\n' "$EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256"
  printf 'C3_EXECUTION_CONTRACT=%q\n' "$C3_EXECUTION_CONTRACT"
  printf 'EXPECTED_C3_EXECUTION_CONTRACT_SHA256=%q\n' "$EXPECTED_C3_EXECUTION_CONTRACT_SHA256"
  printf 'CLOUDSDK_CONFIG=%q\n' "$CLOUDSDK_CONFIG"
  printf 'LVEF_C3_EXPECTED_GCP_ACCOUNT=%q\n' "$LVEF_C3_EXPECTED_GCP_ACCOUNT"
  printf 'LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME=%q\n' "$LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME"
  printf 'LVEF_C3_GCP_AUTHORIZED_USER_FILE=%q\n' "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
} >>"$PREFLIGHT_ENV_NEXT"
mv -f "$PREFLIGHT_ENV_NEXT" "$PREFLIGHT_ENV"
chmod 600 "$PREFLIGHT_ENV"
unset LVEF_C3_EXPECTED_GCP_ACCOUNT
unset LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME
unset LVEF_C3_GCP_AUTHORIZED_USER_FILE
printf '%s\n' 'GCLOUD_RESOLUTION=PASS_CREDENTIALS_NOT_ACCESSED'
```

## 3C. Initialize the isolated owner authentication

This is the only interactive login block. Run it by itself. The `gcloud auth
login` invocation is deliberately the final command so no later shell text can
be consumed by an interactive authentication prompt. It uses only the new
owner-private config directory under the preserved restricted run root. Mode
`0700` is canonical; SCC's inherited setgid-only mode `2700` is also accepted
because group and other permissions remain zero. It does not
read or modify a home-directory Cloud SDK profile. Do not paste another command
until this command has returned to the shell prompt. Any browser URL, device
code, or authentication transcript is restricted and must not be pasted into an
ordinary response or Git.

```bash
set -euo pipefail
umask 077
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"
test -z "${CLOUDSDK_CONFIG:-}"
test ! -L "$SESSION_ENV"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"
"$WORKTREE/scripts/check_lvef_private_directory.sh" "$CLOUDSDK_CONFIG"
test ! -L "$PREFLIGHT_ENV"
test -f "$PREFLIGHT_ENV"
test -O "$PREFLIGHT_ENV"
test "$(stat -c '%a' "$PREFLIGHT_ENV")" = "600"
source "$PREFLIGHT_ENV"
test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"
export -n LVEF_C3_EXPECTED_GCP_ACCOUNT
export -n LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME
export -n LVEF_C3_GCP_BILLING_PROJECT
export -n LVEF_C3_GCP_AUTHORIZED_USER_FILE
test -n "$LVEF_C3_EXPECTED_GCP_ACCOUNT"
test -z "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
CLOUDSDK_CONFIG="$CLOUDSDK_CONFIG" "$GCLOUD" auth login "$LVEF_C3_EXPECTED_GCP_ACCOUNT" --no-launch-browser --update-adc
```

## 3D. Configure and verify prospective authentication, billing, and metadata access

This gate reads the exact expected identity, project display name, and existing
requester-pays project only from the owner-only preflight environment. It rejects
ambient access-token and credential-file overrides, prints no identity, project,
token, credential path, project number, or billing-account identifier, and makes
metadata/control-plane probes only. The CLI bearer token and the isolated ADC
bearer token are independently resolved through OAuth userinfo and must both
match the expected account; the ADC `quota_project_id` must independently match
the prospective project. Detailed evidence, counts, and cryptographic authority
fields stay restricted. The aggregate receipt contains only its structural
schema version, one status string, and reviewed Boolean attestations; it contains
no cloud identifiers, credential paths, hashes, commits, or numeric counts.
Failure of `set-quota-project` first writes a separate aggregate-safe failed-stage
receipt and exits before the authority audit. A failed or partial gate blocks
metadata-job submission without changing completed storage-audit outputs. Every
configuration attempt uses one unique identifier for owner-only stdout, stderr,
and the quota-stage receipt, so a retry never truncates earlier failure evidence.
A partial authority-receipt pair fails before any configuration mutation; a
complete pair is live-validated against its already-bound quota stage without
rewriting the preflight environment or creating a new stage.

```bash
set -euo pipefail
umask 077
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"
test -z "${CLOUDSDK_CONFIG:-}"
test ! -L "$SESSION_ENV"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
test -x "$GCLOUD"
test "$(sha256sum "$GCLOUD_RESOLUTION_RECORD" | awk '{print $1}')" = "$EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256"
test "$(sha256sum "$GCP_AUTHORITY_WRAPPER" | awk '{print $1}')" = "$EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256"
test "$(sha256sum "$C3_EXECUTION_CONTRACT" | awk '{print $1}')" = "$EXPECTED_C3_EXECUTION_CONTRACT_SHA256"
"$WORKTREE/scripts/check_lvef_private_directory.sh" "$CLOUDSDK_CONFIG"
test ! -L "$PREFLIGHT_ENV"
test -f "$PREFLIGHT_ENV"
test -O "$PREFLIGHT_ENV"
test "$(stat -c '%a' "$PREFLIGHT_ENV")" = "600"
source "$PREFLIGHT_ENV"
test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"
export -n LVEF_C3_EXPECTED_GCP_ACCOUNT
export -n LVEF_C3_EXPECTED_GCP_PROJECT_DISPLAY_NAME
export -n LVEF_C3_GCP_BILLING_PROJECT
export -n LVEF_C3_GCP_AUTHORIZED_USER_FILE
GCP_AUTHORITY_RESTRICTED="$RUN_ROOT/restricted/gcp_authority_receipt.restricted.json"
GCP_AUTHORITY_SUMMARY="$RUN_ROOT/aggregate/gcp_authority_receipt.summary.json"
GCP_AUTHORITY_RESTRICTED_EXISTS=0
GCP_AUTHORITY_SUMMARY_EXISTS=0
[[ -e "$GCP_AUTHORITY_RESTRICTED" ]] && GCP_AUTHORITY_RESTRICTED_EXISTS=1
[[ -e "$GCP_AUTHORITY_SUMMARY" ]] && GCP_AUTHORITY_SUMMARY_EXISTS=1
if [[ "$GCP_AUTHORITY_RESTRICTED_EXISTS" -ne "$GCP_AUTHORITY_SUMMARY_EXISTS" ]]; then
  printf '%s\n' 'GCP_PROSPECTIVE_AUTHORITY_GATE=BLOCKED_PARTIAL_RECEIPT_SET_NO_MUTATION' >&2
  exit 73
fi

CANONICAL_ADC_FILE="$CLOUDSDK_CONFIG/application_default_credentials.json"
test ! -L "$CANONICAL_ADC_FILE"
test -f "$CANONICAL_ADC_FILE"
test -O "$CANONICAL_ADC_FILE"
test "$(stat -c '%a' "$CANONICAL_ADC_FILE")" = "600"
if [[ "$GCP_AUTHORITY_RESTRICTED_EXISTS" -eq 1 ]]; then
  test "$LVEF_C3_GCP_AUTHORIZED_USER_FILE" = "$CANONICAL_ADC_FILE"
  test -n "$GCP_QUOTA_PROJECT_STAGE"
  test -n "$EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256"
  test -f "$GCP_QUOTA_PROJECT_STAGE"
  test -O "$GCP_QUOTA_PROJECT_STAGE"
  test "$(stat -c '%a' "$GCP_QUOTA_PROJECT_STAGE")" = "600"
  test "$(sha256sum "$GCP_QUOTA_PROJECT_STAGE" | awk '{print $1}')" = \
    "$EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256"
else
  LVEF_C3_GCP_AUTHORIZED_USER_FILE="$CANONICAL_ADC_FILE"

  GCP_AUTH_ATTEMPT_ID="$(date -u +%Y%m%dT%H%M%S%NZ)_$$_${RANDOM}"
  GCLOUD_AUTH_CONFIG_STDOUT="$RUN_ROOT/restricted/logs/gcloud_auth_configuration_${GCP_AUTH_ATTEMPT_ID}.stdout.txt"
  GCLOUD_AUTH_CONFIG_STDERR="$RUN_ROOT/restricted/logs/gcloud_auth_configuration_${GCP_AUTH_ATTEMPT_ID}.stderr.txt"
  GCP_QUOTA_PROJECT_STAGE="$RUN_ROOT/aggregate/gcp_adc_quota_project_setup_${GCP_AUTH_ATTEMPT_ID}.summary.json"
  test ! -e "$GCLOUD_AUTH_CONFIG_STDOUT"
  test ! -e "$GCLOUD_AUTH_CONFIG_STDERR"
  test ! -e "$GCP_QUOTA_PROJECT_STAGE"
  ( set -o noclobber; : >"$GCLOUD_AUTH_CONFIG_STDOUT" )
  ( set -o noclobber; : >"$GCLOUD_AUTH_CONFIG_STDERR" )
  chmod 600 "$GCLOUD_AUTH_CONFIG_STDOUT" "$GCLOUD_AUTH_CONFIG_STDERR"
  CLOUDSDK_CONFIG="$CLOUDSDK_CONFIG" "$GCLOUD" config set account "$LVEF_C3_EXPECTED_GCP_ACCOUNT" \
    >>"$GCLOUD_AUTH_CONFIG_STDOUT" 2>>"$GCLOUD_AUTH_CONFIG_STDERR"
  CLOUDSDK_CONFIG="$CLOUDSDK_CONFIG" "$GCLOUD" config set project "$LVEF_C3_GCP_BILLING_PROJECT" \
    >>"$GCLOUD_AUTH_CONFIG_STDOUT" 2>>"$GCLOUD_AUTH_CONFIG_STDERR"

  set +e
  CLOUDSDK_CONFIG="$CLOUDSDK_CONFIG" "$GCLOUD" auth application-default set-quota-project "$LVEF_C3_GCP_BILLING_PROJECT" \
    >>"$GCLOUD_AUTH_CONFIG_STDOUT" 2>>"$GCLOUD_AUTH_CONFIG_STDERR"
  GCP_QUOTA_PROJECT_COMMAND_STATUS=$?
  set -e
  if "$PYTHON" scripts/write_lvef_gcp_quota_project_stage.py \
    --command-exit-code "$GCP_QUOTA_PROJECT_COMMAND_STATUS" \
    --output "$GCP_QUOTA_PROJECT_STAGE"; then
    GCP_QUOTA_PROJECT_STAGE_WRITER_STATUS=0
  else
    GCP_QUOTA_PROJECT_STAGE_WRITER_STATUS=$?
  fi
  test -f "$GCP_QUOTA_PROJECT_STAGE"
  test -O "$GCP_QUOTA_PROJECT_STAGE"
  test "$(stat -c '%a' "$GCP_QUOTA_PROJECT_STAGE")" = "600"
  if [[ "$GCP_QUOTA_PROJECT_COMMAND_STATUS" -ne 0 ]]; then
    test "$GCP_QUOTA_PROJECT_STAGE_WRITER_STATUS" -eq 3
    printf '%s\n' 'GCP_ADC_QUOTA_PROJECT_SETUP=FAIL_AUTHORITY_AUDIT_NOT_STARTED' >&2
    exit "$GCP_QUOTA_PROJECT_COMMAND_STATUS"
  fi
  test "$GCP_QUOTA_PROJECT_STAGE_WRITER_STATUS" -eq 0
  EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256="$(sha256sum "$GCP_QUOTA_PROJECT_STAGE" | awk '{print $1}')"
  [[ "$EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256" =~ ^[0-9a-f]{64}$ ]]

  PREFLIGHT_ENV_NEXT="$(mktemp "$PREFLIGHT_ENV.tmp.XXXXXX")"
  chmod 600 "$PREFLIGHT_ENV_NEXT"
  awk '
    !/^LVEF_C3_GCP_AUTHORIZED_USER_FILE=/ &&
    !/^GCP_QUOTA_PROJECT_STAGE=/ &&
    !/^EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256=/
  ' "$PREFLIGHT_ENV" >"$PREFLIGHT_ENV_NEXT"
  {
    printf 'LVEF_C3_GCP_AUTHORIZED_USER_FILE=%q\n' "$LVEF_C3_GCP_AUTHORIZED_USER_FILE"
    printf 'GCP_QUOTA_PROJECT_STAGE=%q\n' "$GCP_QUOTA_PROJECT_STAGE"
    printf 'EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256=%q\n' "$EXPECTED_GCP_QUOTA_PROJECT_STAGE_SHA256"
  } >>"$PREFLIGHT_ENV_NEXT"
  mv -f "$PREFLIGHT_ENV_NEXT" "$PREFLIGHT_ENV"
  chmod 600 "$PREFLIGHT_ENV"
fi

CLOUDSDK_CONFIG="$CLOUDSDK_CONFIG" "$GCP_AUTHORITY_WRAPPER" \
  --preflight-env "$PREFLIGHT_ENV" \
  --restricted-output "$GCP_AUTHORITY_RESTRICTED" \
  --aggregate-output "$GCP_AUTHORITY_SUMMARY"
test -f "$GCP_AUTHORITY_RESTRICTED"
test -f "$GCP_AUTHORITY_SUMMARY"
test "$(stat -c '%a' "$GCP_AUTHORITY_RESTRICTED")" = "600"
printf '%s\n' 'GCP_PROSPECTIVE_AUTHORITY_GATE=PASS_METADATA_ONLY'
```

## 4. Submit only the metadata/resource preflight

No ambient shell environment is exported to the job.

```bash
set -euo pipefail
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test ! -L "$SESSION_ENV"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
test -z "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}"
test -z "${GOOGLE_APPLICATION_CREDENTIALS:-}"
test -z "${CLOUDSDK_CORE_ACCOUNT:-}"
test -z "${CLOUDSDK_CORE_PROJECT:-}"
test -z "${CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE:-}"
test -z "${CLOUDSDK_AUTH_ACCESS_TOKEN_FILE:-}"
test -z "${CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT:-}"
test -z "${CLOUDSDK_AUTH_DELEGATES:-}"
test -x "$GCLOUD"
test "$(sha256sum "$GCLOUD_RESOLUTION_RECORD" | awk '{print $1}')" = "$EXPECTED_GCLOUD_RESOLUTION_RECORD_SHA256"
test "$(sha256sum "$GCP_AUTHORITY_WRAPPER" | awk '{print $1}')" = "$EXPECTED_GCP_AUTHORITY_WRAPPER_SHA256"
test "$(sha256sum "$C3_EXECUTION_CONTRACT" | awk '{print $1}')" = "$EXPECTED_C3_EXECUTION_CONTRACT_SHA256"
"$WORKTREE/scripts/check_lvef_private_directory.sh" "$CLOUDSDK_CONFIG"
CLOUDSDK_CONFIG="$CLOUDSDK_CONFIG" "$GCP_AUTHORITY_WRAPPER" \
  --preflight-env "$PREFLIGHT_ENV" \
  --restricted-output "$RUN_ROOT/restricted/gcp_authority_receipt.restricted.json" \
  --aggregate-output "$RUN_ROOT/aggregate/gcp_authority_receipt.summary.json"
qsub \
  -P mimicecho \
  -N lvef_c3_preflight \
  -cwd \
  -l h_rt=24:00:00 \
  -l mem_total=16G \
  -o "$RUN_ROOT/scheduler_logs" \
  -e "$RUN_ROOT/scheduler_logs" \
  -v "LVEF_C3_PREFLIGHT_ENV_FILE=$PREFLIGHT_ENV" \
  "$WORKTREE/scripts/scc_run_lvef_c3_resource_preflight.sh"
```

The job is metadata-only. It calls Cloud Storage bucket metadata and paginated `objects.list` GETs, never `alt=media`, `objects.get` media, `gsutil cp`, or `gcloud storage cp`. The requester-pays value is quarantined after the owner-only environment file is sourced, exposed only to the source-preflight Python subprocess through its environment, and cleared afterward; it is never passed in argv or printed. A partially written final output set blocks reuse; preserve the failed root and start a fresh run rather than deleting evidence in place.

## 5. Build and submit the restricted technical-metadata packet

```bash
set -euo pipefail
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
PHASE1D_CLINICAL_ROOT="/restricted/project/mimicecho/audits/lvef_multitask_phase1d_clinical_final_20260804T001847Z_e97324a4e4a3"
CLINICAL_REVIEW_ROWS="$PHASE1D_CLINICAL_ROOT/restricted/clinical_metadata/clinical_metadata_review_rows_restricted.csv"
CLINICAL_PACKET_MANIFEST="$PHASE1D_CLINICAL_ROOT/aggregate/clinical_metadata/clinical_metadata_review_packet_manifest.json"
RAW_CANONICAL_MAPPING="$FULLSCALE_ROOT/measurement_registry_v1/measurement_to_canonical_mapping.csv"
STRUCTURED_MEASUREMENTS="$FULLSCALE_ROOT/manifests/structured_measurements.csv"
EXPECTED_CLINICAL_REVIEW_ROWS_SHA256="$(
  "$PYTHON" -c '
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
matches = [row["sha256"] for row in payload["restricted_output_files"] if row["relative_path"] == "clinical_metadata_review_rows_restricted.csv"]
assert len(matches) == 1 and len(matches[0]) == 64
print(matches[0])
' "$CLINICAL_PACKET_MANIFEST"
)"
test "$(sha256sum "$CLINICAL_REVIEW_ROWS" | awk '{print $1}')" = "$EXPECTED_CLINICAL_REVIEW_ROWS_SHA256"
EXPECTED_RAW_CANONICAL_MAPPING_SHA256="2f1b6c424c62c39017396130fe074accce06cbb05e1977b4c27144e0f824fd18"
EXPECTED_STRUCTURED_MEASUREMENTS_SHA256="95fc852457c25ca548d6fa1ae3ec5d2740b99a6aa3d53297a5b424ffc3d27023"
test "$(sha256sum "$RAW_CANONICAL_MAPPING" | awk '{print $1}')" = "$EXPECTED_RAW_CANONICAL_MAPPING_SHA256"
test "$(sha256sum "$STRUCTURED_MEASUREMENTS" | awk '{print $1}')" = "$EXPECTED_STRUCTURED_MEASUREMENTS_SHA256"

METADATA_RUN_ROOT="${RUN_ROOT}_metadata"
METADATA_SCHEDULER_LOG_ROOT="$RUN_ROOT/scheduler_logs_metadata"
METADATA_ENV="$RUN_ROOT/restricted/lvef_metadata_adjudication.env"
install -m 600 /dev/null "$METADATA_ENV"
{
  printf 'WORKTREE=%q\n' "$WORKTREE"
  printf 'EXPECTED_COMMIT=%q\n' "$EXPECTED_COMMIT"
  printf 'PYTHON=%q\n' "$PYTHON"
  printf 'SAFE_EXPORT_POLICY=%q\n' "$SAFE_EXPORT_POLICY"
  printf 'EXPECTED_SAFE_EXPORT_POLICY_SHA256=%q\n' "$EXPECTED_SAFE_EXPORT_POLICY_SHA256"
  printf 'CLINICAL_REVIEW_ROWS=%q\n' "$CLINICAL_REVIEW_ROWS"
  printf 'EXPECTED_CLINICAL_REVIEW_ROWS_SHA256=%q\n' "$EXPECTED_CLINICAL_REVIEW_ROWS_SHA256"
  printf 'RAW_CANONICAL_MAPPING=%q\n' "$RAW_CANONICAL_MAPPING"
  printf 'EXPECTED_RAW_CANONICAL_MAPPING_SHA256=%q\n' "$EXPECTED_RAW_CANONICAL_MAPPING_SHA256"
  printf 'STRUCTURED_MEASUREMENTS=%q\n' "$STRUCTURED_MEASUREMENTS"
  printf 'EXPECTED_STRUCTURED_MEASUREMENTS_SHA256=%q\n' "$EXPECTED_STRUCTURED_MEASUREMENTS_SHA256"
  printf 'SELECTED_STUDIES=%q\n' "$SELECTED_STUDIES"
  printf 'EXPECTED_SELECTED_STUDIES_SHA256=%q\n' "$EXPECTED_SELECTED_STUDIES_SHA256"
  printf 'SPLIT_MAP=%q\n' "$SPLIT_MAP"
  printf 'EXPECTED_SPLIT_MAP_SHA256=%q\n' "$EXPECTED_SPLIT_MAP_SHA256"
  printf 'APPROVED_ORGANIZATION_CLASS=%q\n' 'institutionally_managed_enterprise_edu_healthcare_or_api_organization'
  printf 'RUN_ROOT=%q\n' "$METADATA_RUN_ROOT"
} >"$METADATA_ENV"
chmod 600 "$METADATA_ENV"
mkdir -p "$METADATA_SCHEDULER_LOG_ROOT"
SESSION_ENV_NEXT="$(mktemp "$SESSION_ENV.tmp.XXXXXX")"
chmod 600 "$SESSION_ENV_NEXT"
cp "$SESSION_ENV" "$SESSION_ENV_NEXT"
{
  printf 'METADATA_RUN_ROOT=%q\n' "$METADATA_RUN_ROOT"
  printf 'METADATA_SCHEDULER_LOG_ROOT=%q\n' "$METADATA_SCHEDULER_LOG_ROOT"
  printf 'METADATA_ENV=%q\n' "$METADATA_ENV"
} >>"$SESSION_ENV_NEXT"
mv -f "$SESSION_ENV_NEXT" "$SESSION_ENV"
chmod 600 "$SESSION_ENV"

qsub \
  -P mimicecho \
  -N lvef_metadata \
  -cwd \
  -l h_rt=08:00:00 \
  -l mem_total=32G \
  -o "$METADATA_SCHEDULER_LOG_ROOT" \
  -e "$METADATA_SCHEDULER_LOG_ROOT" \
  -v "LVEF_METADATA_ADJUDICATION_ENV_FILE=$METADATA_ENV" \
  "$WORKTREE/scripts/scc_run_lvef_metadata_adjudication.sh"
```

The generated eight-question packet and response template stay restricted. Human echocardiographer signoff is not implied by successful packet generation.

## 6. Aggregate-safe outputs to report

After both jobs finish, inspect these aggregate files on SCC. They may be summarized in an ordinary response; entering Git still requires the separate reviewed-export flow.

```bash
set -euo pipefail
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
for path in \
  "$RUN_ROOT/aggregate/scc_storage_inventory.summary.json" \
  "$RUN_ROOT/aggregate/c3_full_source_preflight.summary.json" \
  "$RUN_ROOT/aggregate/c3_full_source_preflight_by_batch.csv" \
  "$RUN_ROOT/aggregate/c3_full_source_cost_estimate.json" \
  "$RUN_ROOT/aggregate/c3_full_source_preflight_safety_gate.json" \
  "$RUN_ROOT/aggregate/c3_full_resource_plan.json" \
  "$METADATA_RUN_ROOT/aggregate/technical_metadata/technical_metadata_issue_summary.csv" \
  "$METADATA_RUN_ROOT/aggregate/technical_metadata/lvef_label_definition_counts.csv" \
  "$METADATA_RUN_ROOT/aggregate/technical_metadata/lvef_separate_label_authority.json" \
  "$METADATA_RUN_ROOT/aggregate/technical_metadata/technical_metadata_safety_gate.json"; do
  test -f "$path"
  printf '%s\t%s\t%s\n' "$(basename "$path")" "$(stat -c '%s' "$path")" "$(sha256sum "$path" | awk '{print $1}')"
done
```

Do not paste the restricted source-object JSONL, discrepancy rows, quota/path inventory, clinical raw metadata, unit distributions, pair/formula diagnostics, signoff packet, response template, receipt, environment files, or scheduler/application logs.

## 7. Full C3 command printer — intentionally NO-GO

The structural contract can print the future 19-task, concurrency-one array and dependent-finalizer topology. It cannot submit either job in this phase.

```bash
set -euo pipefail
SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
test -f "$SESSION_ENV"
test -O "$SESSION_ENV"
test "$(stat -c '%a' "$SESSION_ENV")" = "600"
source "$SESSION_ENV"
cd "$WORKTREE"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
LVEF_C3_PYTHON="$PYTHON" \
  "$WORKTREE/scripts/scc_submit_lvef_c3_full.sh" \
  "$WORKTREE/configs/lvef_c3_execution_contract.yaml" \
  - \
  /restricted/projectnb/mimicecho/lvef_multitask_c3/restricted/c3_execution.env \
  --print-only
```

`--submit` is expected to exit 78. The production batch runner, exact-generation body downloader, full-batch preservation authority, cross-batch finalizer, migrated environment, enriched selected-source freeze, resource/budget approvals, and owner authorization are still absent.
