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

export LVEF_SCC_PYTHON="/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python"
PYTHON="$(scripts/resolve_lvef_scc_python.sh)"
test -x "$PYTHON"
test "$(sha256sum "$PYTHON" | awk '{print $1}')" = "1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb"

SESSION_ENV="/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_session.env"
mkdir -p "$(dirname "$SESSION_ENV")"
umask 077
SESSION_ENV_NEXT="$(mktemp "$SESSION_ENV.tmp.XXXXXX")"
chmod 600 "$SESSION_ENV_NEXT"
{
  printf 'WORKTREE=%q\n' "$WORKTREE"
  printf 'EXPECTED_COMMIT=%q\n' "$EXPECTED_COMMIT"
  printf 'PYTHON=%q\n' "$PYTHON"
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

Block 2 creates only a conservative planning witness. It classifies the entire current disaster-tier inventory for migration after a separately verified backup, records `PLANNED_NOT_EXECUTED`, and explicitly records that neither backup nor migration was completed. The restricted path-level classification and its witness are checksum-bound to the current storage-detail JSON. The 50-GB retention option remains provisional and is not used in this full-migration planning mode.

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

## 4. Submit only the metadata/resource preflight

No ambient shell environment is exported to the job.

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

The job is metadata-only. It calls Cloud Storage bucket metadata and paginated `objects.list` GETs, never `alt=media`, `objects.get` media, `gsutil cp`, or `gcloud storage cp`. A partially written final output set blocks reuse; preserve the failed root and start a fresh run rather than deleting evidence in place.

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
