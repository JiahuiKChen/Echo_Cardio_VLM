# Prospective full C3 production scheduler commands

Status: **UNEXECUTED — NO PRODUCTION AUTHORITY GRANTED**.

This is the frozen command topology for a future, separately authorized
selected-cohort reconstruction. Phase 1E-E did not run any command below,
submit `qsub`, access Google Cloud, download an object body, decode a real
DICOM, extract a cine, run EchoPrime, generate an embedding, fit a model, or
access confirmatory performance.

The authority-bound SCC worktree is:

```text
/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
```

Every future invocation requires an owner-private mode-600 base execution
environment, a last-created owner-private launch-authority envelope, a
qsub-dispatch authorization receipt, and a separate operation-specific
scientific authorization receipt. The dispatch and scientific receipts have
different closed schemas and must never be reused for one another. The base
execution environment binds the exact governing commit,
orchestration contract, 19-batch plan, EchoPrime Python/environment/checkpoint
authorities, isolated Cloud SDK configuration, production root, attempt ID,
authorization-receipt roots, and the separately pinned Cloud-SDK-bundled
Python plus compiled CRC32C worker authority. The two Python runtimes are not
interchangeable: EchoPrime stays on the validated 3.10 environment, while the
isolated 3.14 helper performs only one-pass SHA-256/MD5/CRC32C verification.
The requester-pays project remains private;
it is never placed in an argument, committed file, aggregate log, or scheduler
environment export. The dispatcher never uses `qsub -V`.

The launch envelope resolves the unavoidable non-circular ordering: the base
environment is created first, the authority packet binds that environment,
and the launch envelope is created last to bind the environment, passing live
capacity summary, and packet by path, size, and SHA-256. Both the dispatcher
and scheduled runner revalidate the envelope before any stage can begin.

## Authorization scopes

These scopes are independent and must not be combined implicitly:

1. first-batch DICOM body transfer;
2. remaining-batch DICOM body transfer;
3. per-batch DICOM audit and extraction;
4. per-batch EchoPrime inference and study pooling;
5. per-batch preservation and separately authorized cache retirement;
6. model fitting;
7. confirmatory-test access.

Reconstruction authority never grants scopes 6 or 7. Raw-DICOM deletion is
not implemented as an authorized operation. Extracted-cache retirement is a
separate owner-authorized transaction after preservation; it is not implied by
extraction, embedding, or preservation success.

## Offline authority preparation

This exact preparation topology is **UNEXECUTED**. It is permitted only at a
clean, synchronized, owner-reviewed commit. It makes no cloud request and
grants no production authorization. Values in angle brackets are restricted
SCC authorities; they are never printed or committed.

```bash
set -euo pipefail
umask 077

AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
GOVERNING_COMMIT="$(git -C "$AUTHORITY_WORKTREE" rev-parse HEAD)"
PY='/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python'
CRC32C_PY='/restricted/projectnb/mimicecho/tools/google-cloud-cli-579.0.0/google-cloud-sdk/platform/bundledpythonunix/bin/python3.14'
CRC32C_PY_SHA256='52a2a75599d1bbbd1f5705af946fc3ffbd68b5430adcda0dea2d0a00b33fd1b5'
CRC32C_WORKER="$AUTHORITY_WORKTREE/scripts/lvef_c3_crc32c_worker.py"
PRODUCTION_ROOT='/restricted/projectnb/mimicecho/lvef_multitask_c3_v2'
ATTEMPT_ID='<NEW_NO_CLOBBER_PHASE1EE_PRODUCTION_ATTEMPT_ID>'
AUTHORITY_INPUT_ROOT='<OWNER_PRIVATE_PROJECTNB_AUTHORITY_INPUT_ROOT>'
ENVIRONMENT_RECEIPT="$AUTHORITY_INPUT_ROOT/production_environment.restricted.json"
CAPACITY_SUMMARY='<CURRENT_PASSING_POST_EXPANSION_CAPACITY_SUMMARY>'
GCLOUD='/restricted/projectnb/mimicecho/tools/google-cloud-cli-579.0.0/google-cloud-sdk/bin/gcloud'
GCLOUD_RECEIPT='<OWNER_PRIVATE_GCLOUD_RESOLUTION_RECEIPT>'
CLOUDSDK_CONFIG='<OWNER_PRIVATE_ISOLATED_CLOUDSDK_CONFIG>'

"$PY" "$AUTHORITY_WORKTREE/scripts/capture_lvef_c3_production_environment.py" \
  --prior-environment '<PINNED_PHASE1EA_ENVIRONMENT_RECEIPT>' \
  --governing-commit "$GOVERNING_COMMIT" \
  --checkout-root "$AUTHORITY_WORKTREE" \
  --crc32c-python "$CRC32C_PY" \
  --crc32c-python-expected-sha256 "$CRC32C_PY_SHA256" \
  --crc32c-worker "$CRC32C_WORKER" \
  --output "$ENVIRONMENT_RECEIPT"

# The requester-pays project must already be a nonexported owner-private shell
# value. Prefix export lasts for this offline subprocess only; it is not argv.
: "${LVEF_C3_GCP_BILLING_PROJECT:?owner-private value required}"
export -n LVEF_C3_GCP_BILLING_PROJECT
LVEF_C3_GCP_BILLING_PROJECT="$LVEF_C3_GCP_BILLING_PROJECT" \
"$PY" "$AUTHORITY_WORKTREE/scripts/prepare_lvef_c3_production_control_plane.py" \
  --governing-commit "$GOVERNING_COMMIT" \
  --attempt-id "$ATTEMPT_ID" \
  --checkout-root "$AUTHORITY_WORKTREE" \
  --contract "$AUTHORITY_WORKTREE/configs/lvef_c3_orchestration_v2.yaml" \
  --selected '<FROZEN_SELECTED_STUDY_MANIFEST>' \
  --source '<FROZEN_SELECTED_SOURCE_MANIFEST>' \
  --source-metadata '<IMMUTABLE_JOB_7104307_SELECTED_SOURCE_METADATA_RECEIPT>' \
  --split '<FROZEN_SUBJECT_SPLIT_MAP>' \
  --checkpoint '<PINNED_ECHOPRIME_CHECKPOINT>' \
  --environment-receipt "$ENVIRONMENT_RECEIPT" \
  --crc32c-python "$CRC32C_PY" \
  --crc32c-python-expected-sha256 "$CRC32C_PY_SHA256" \
  --crc32c-worker "$CRC32C_WORKER" \
  --state-machine-schema "$AUTHORITY_WORKTREE/configs/lvef_c3_state_machine_v2.json" \
  --resume-ledger-schema "$AUTHORITY_WORKTREE/configs/lvef_c3_resume_ledger_v2.json" \
  --gcloud-executable "$GCLOUD" \
  --gcloud-resolution-receipt "$GCLOUD_RECEIPT" \
  --cloudsdk-config "$CLOUDSDK_CONFIG" \
  --cloudsdk-config-receipt "$GCLOUD_RECEIPT" \
  --python-sha256 1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb \
  --production-root "$PRODUCTION_ROOT" \
  --extraction-workers 4 \
  --embedding-batch-size 8
unset LVEF_C3_GCP_BILLING_PROJECT

ATTEMPT_ROOT="$PRODUCTION_ROOT/attempts/$ATTEMPT_ID"
EXECUTION_ENV="$ATTEMPT_ROOT/authority/c3_execution_environment.restricted.env"
BATCH_PLAN="$ATTEMPT_ROOT/authority/batch_plan.restricted.json"
AUTHORITY_PACKET="$ATTEMPT_ROOT/authority/lvef_c3_production_authority_packet.restricted.json"

# Exactly all 38 closed authority roles. No role may be omitted or duplicated.
"$PY" "$AUTHORITY_WORKTREE/scripts/build_lvef_c3_production_authority_packet.py" \
  --governing-commit "$GOVERNING_COMMIT" \
  --checkout-root "$AUTHORITY_WORKTREE" \
  --attempt-id "$ATTEMPT_ID" \
  --artifact "aggregate_export_policy=$AUTHORITY_WORKTREE/configs/lvef_multitask_safe_export_policy.yaml" \
  --artifact "authority_packet_builder=$AUTHORITY_WORKTREE/scripts/build_lvef_c3_production_authority_packet.py" \
  --artifact "batch_plan=$BATCH_PLAN" \
  --artifact "batch_preservation_producer=$AUTHORITY_WORKTREE/scripts/preserve_lvef_c3_production_batch.py" \
  --artifact "cache_retirement_gate=$AUTHORITY_WORKTREE/scripts/retire_lvef_c3_extracted_cache_v2.py" \
  --artifact "checkpoint=<PINNED_ECHOPRIME_CHECKPOINT>" \
  --artifact "cloudsdk_config_receipt=$GCLOUD_RECEIPT" \
  --artifact "control_plane_preparer=$AUTHORITY_WORKTREE/scripts/prepare_lvef_c3_production_control_plane.py" \
  --artifact "crc32c_python_executable=$CRC32C_PY" \
  --artifact "crc32c_worker=$CRC32C_WORKER" \
  --artifact "dicom_audit_and_extractor=$AUTHORITY_WORKTREE/scripts/lvef_c3_production_stages.py" \
  --artifact "downloader=$AUTHORITY_WORKTREE/scripts/lvef_c3_orchestration_core.py" \
  --artifact "echoprime_wrapper=$AUTHORITY_WORKTREE/scripts/lvef_c3_production_stages.py" \
  --artifact "environment_receipt=$ENVIRONMENT_RECEIPT" \
  --artifact "environment_receipt_capture=$AUTHORITY_WORKTREE/scripts/capture_lvef_c3_production_environment.py" \
  --artifact "execution_environment=$EXECUTION_ENV" \
  --artifact "finalizer=$AUTHORITY_WORKTREE/scripts/finalize_lvef_c3_production.py" \
  --artifact "future_command_block=$AUTHORITY_WORKTREE/docs/lvef_multitask/scc_phase1ee_production_commands.md" \
  --artifact "gcloud_executable=$GCLOUD" \
  --artifact "gcloud_resolution_receipt=$GCLOUD_RECEIPT" \
  --artifact "launch_authority_builder=$AUTHORITY_WORKTREE/scripts/build_lvef_c3_production_launch_authority.py" \
  --artifact "orchestration_contract=$AUTHORITY_WORKTREE/configs/lvef_c3_orchestration_v2.yaml" \
  --artifact "owner_authorization_builder=$AUTHORITY_WORKTREE/scripts/build_lvef_c3_owner_authorization_receipt.py" \
  --artifact "post_expansion_capacity_summary=$CAPACITY_SUMMARY" \
  --artifact "preservation_policy=$AUTHORITY_WORKTREE/configs/lvef_c3_preservation_policy_v2.yaml" \
  --artifact "prior_batch_finalization_validator=$AUTHORITY_WORKTREE/scripts/validate_lvef_c3_prior_batch_finalization.py" \
  --artifact "python_executable=$PY" \
  --artifact "resume_ledger_schema=$AUTHORITY_WORKTREE/configs/lvef_c3_resume_ledger_v2.json" \
  --artifact "scheduler_batch_runner=$AUTHORITY_WORKTREE/scripts/scc_run_lvef_c3_production_batch_v2.sh" \
  --artifact "scheduler_common=$AUTHORITY_WORKTREE/scripts/lvef_c3_production_scheduler_common.sh" \
  --artifact "scheduler_dispatch_authorization_validator=$AUTHORITY_WORKTREE/scripts/validate_lvef_c3_dispatch_authorization.py" \
  --artifact "scheduler_dispatcher=$AUTHORITY_WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh" \
  --artifact "scheduler_finalizer=$AUTHORITY_WORKTREE/scripts/scc_finalize_lvef_c3_production_v2.sh" \
  --artifact "selected_source_manifest=<FROZEN_SELECTED_SOURCE_MANIFEST>" \
  --artifact "selected_source_metadata_receipt=<IMMUTABLE_JOB_7104307_SELECTED_SOURCE_METADATA_RECEIPT>" \
  --artifact "selected_study_manifest=<FROZEN_SELECTED_STUDY_MANIFEST>" \
  --artifact "split_map=<FROZEN_SUBJECT_SPLIT_MAP>" \
  --artifact "state_machine_schema=$AUTHORITY_WORKTREE/configs/lvef_c3_state_machine_v2.json" \
  --output "$AUTHORITY_PACKET"

# This last-created envelope is impossible while any byte-capacity,
# file-quota, physical-filesystem, reserve, or backed-control gate is false.
LAUNCH_AUTHORITY="$ATTEMPT_ROOT/authority/lvef_c3_production_launch_authority.restricted.json"
"$PY" "$AUTHORITY_WORKTREE/scripts/build_lvef_c3_production_launch_authority.py" build \
  --attempt-id "$ATTEMPT_ID" \
  --governing-commit "$GOVERNING_COMMIT" \
  --execution-environment "$EXECUTION_ENV" \
  --capacity-summary "$CAPACITY_SUMMARY" \
  --authority-packet "$AUTHORITY_PACKET" \
  --launch-authority "$LAUNCH_AUTHORITY"
```

The preparation command creates the restricted immutable 19-batch plan, one
initial batch-scoped ledger per batch, closed runtime authority, mode-600
execution environment, separate empty dispatch and operation-authorization
roots, and two aggregate-safe summaries. The packet and launch envelope do not
authorize a stage. The owner must later create exactly one dispatch receipt and
one operation receipt for each bounded action. The sole exception is that the
cross-batch finalizer's operation receipt is scoped to `all_batches`.

## Future dispatcher topology

The following blocks are exact but **UNEXECUTED**. Placeholders identify
owner-private SCC files or scheduler IDs that must be created or returned only
after the corresponding bounded authorization. They are not shell defaults.

```bash
set -euo pipefail
umask 077

AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
PY='/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python'
DISPATCHER="$AUTHORITY_WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh"
EXECUTION_ENV='<OWNER_PRIVATE_MODE_600_EXECUTION_ENV>'
LAUNCH_AUTHORITY='<OWNER_PRIVATE_MODE_600_FINAL_LAUNCH_AUTHORITY>'
AUTH_BUILDER="$AUTHORITY_WORKTREE/scripts/build_lvef_c3_owner_authorization_receipt.py"
ATTEMPT_ROOT='<OWNER_PRIVATE_ATTEMPT_ROOT_ON_PROJECTNB>'
DISPATCH_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/dispatch"
DOWNLOAD_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/download"
EXTRACTION_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/extraction"
ECHOPRIME_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/echoprime"
PRESERVATION_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/preservation"
CACHE_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/cache_retirement"
FINALIZATION_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/finalization"
OWNER_DATE='<YYYY-MM-DD>'

dispatch_one() {
  local job_id
  # The dispatcher writes a status marker to stderr and exactly one bare,
  # validated scheduler identifier to stdout.
  job_id="$($DISPATCHER "$@")"
  test "$(printf '%s\n' "$job_id" | awk '/^[0-9]+([.][0-9-]+:[0-9]+)?$/ {count += 1} END {print count + 0}')" -eq 1
  printf '%s\n' "$job_id"
}

# Scope 1 requires two different receipts: qsub dispatch and body transfer.
FIRST_DOWNLOAD_DISPATCH_AUTH="$DISPATCH_AUTH_ROOT/FIRST_BATCH_DOWNLOAD.1.dispatch_authorization.json"
FIRST_DOWNLOAD_BODY_AUTH="$DOWNLOAD_AUTH_ROOT/c3_batch_000.authorization.json"
"$PY" "$AUTH_BUILDER" dispatch \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage FIRST_BATCH_DOWNLOAD --task-scope 1 \
  --output "$FIRST_DOWNLOAD_DISPATCH_AUTH"
"$PY" "$AUTH_BUILDER" body-transfer \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --batch-id c3_batch_000 --issued-at-utc '<UTC_ISSUED_Z>' \
  --expires-at-utc '<UTC_EXPIRY_Z>' --output "$FIRST_DOWNLOAD_BODY_AUTH"
FIRST_DOWNLOAD_JOB="$(dispatch_one --submit FIRST_BATCH_DOWNLOAD \
  "$EXECUTION_ENV" "$LAUNCH_AUTHORITY" "$FIRST_DOWNLOAD_DISPATCH_AUTH" - 1)"
```

Scope 2 for each remaining batch is authorized and dispatched **one batch at a
time** with the next block. Set `N` to exactly one integer in 2..19. The next
download is gated by the prior batch's verified finalization receipts, which
the runner revalidates before transport; no SGE hold is placed on an already
completed job. Raw/extraction work therefore does not overlap across batches.
Do not automate or advance this sequence without reviewing the prior batch
receipts.

```bash
set -euo pipefail
umask 077

AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
PY='/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python'
DISPATCHER="$AUTHORITY_WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh"
EXECUTION_ENV='<OWNER_PRIVATE_MODE_600_EXECUTION_ENV>'
LAUNCH_AUTHORITY='<OWNER_PRIVATE_MODE_600_FINAL_LAUNCH_AUTHORITY>'
AUTH_BUILDER="$AUTHORITY_WORKTREE/scripts/build_lvef_c3_owner_authorization_receipt.py"
ATTEMPT_ROOT='<OWNER_PRIVATE_ATTEMPT_ROOT_ON_PROJECTNB>'
DISPATCH_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/dispatch"
DOWNLOAD_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/download"
EXTRACTION_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/extraction"
ECHOPRIME_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/echoprime"
PRESERVATION_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/preservation"
OWNER_DATE='<YYYY-MM-DD>'

dispatch_one() {
  local job_id
  job_id="$("$DISPATCHER" "$@")"
  test "$(printf '%s\n' "$job_id" | awk '/^[0-9]+([.][0-9-]+:[0-9]+)?$/ {count += 1} END {print count + 0}')" -eq 1
  printf '%s\n' "$job_id"
}

N='<ONE_BATCH_TASK_NUMBER_2_TO_19>'
printf -v BATCH_ID 'c3_batch_%03d' "$((N - 1))"
REMAINING_DOWNLOAD_DISPATCH_AUTH="$DISPATCH_AUTH_ROOT/REMAINING_BATCH_DOWNLOAD.$N.dispatch_authorization.json"
REMAINING_DOWNLOAD_AUTH="$DOWNLOAD_AUTH_ROOT/$BATCH_ID.authorization.json"
"$PY" "$AUTH_BUILDER" dispatch \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage REMAINING_BATCH_DOWNLOAD --task-scope "$N" \
  --output "$REMAINING_DOWNLOAD_DISPATCH_AUTH"
"$PY" "$AUTH_BUILDER" body-transfer \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --batch-id "$BATCH_ID" --issued-at-utc '<UTC_ISSUED_Z>' \
  --expires-at-utc '<UTC_EXPIRY_Z>' --output "$REMAINING_DOWNLOAD_AUTH"
REMAINING_DOWNLOAD_JOB="$(dispatch_one --submit REMAINING_BATCH_DOWNLOAD \
  "$EXECUTION_ENV" "$LAUNCH_AUTHORITY" "$REMAINING_DOWNLOAD_DISPATCH_AUTH" \
  - "$N")"
```

Only after one first- or remaining-batch download completes, its evidence
independently passes, and the owner separately authorizes extraction may this
generic one-batch block be used. Set `N` to the completed batch's exact task
number in 1..19.

```bash
set -euo pipefail
umask 077

AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
PY='/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python'
DISPATCHER="$AUTHORITY_WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh"
EXECUTION_ENV='<OWNER_PRIVATE_MODE_600_EXECUTION_ENV>'
LAUNCH_AUTHORITY='<OWNER_PRIVATE_MODE_600_FINAL_LAUNCH_AUTHORITY>'
AUTH_BUILDER="$AUTHORITY_WORKTREE/scripts/build_lvef_c3_owner_authorization_receipt.py"
ATTEMPT_ROOT='<OWNER_PRIVATE_ATTEMPT_ROOT_ON_PROJECTNB>'
DISPATCH_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/dispatch"
EXTRACTION_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/extraction"
OWNER_DATE='<YYYY-MM-DD>'
N='<ONE_COMPLETED_BATCH_TASK_NUMBER_1_TO_19>'
printf -v BATCH_ID 'c3_batch_%03d' "$((N - 1))"

dispatch_one() {
  local job_id
  job_id="$("$DISPATCHER" "$@")"
  test "$(printf '%s\n' "$job_id" | awk '/^[0-9]+([.][0-9-]+:[0-9]+)?$/ {count += 1} END {print count + 0}')" -eq 1
  printf '%s\n' "$job_id"
}

EXTRACTION_DISPATCH_AUTH="$DISPATCH_AUTH_ROOT/DICOM_EXTRACTION.$N.dispatch_authorization.json"
EXTRACTION_AUTH="$EXTRACTION_AUTH_ROOT/$BATCH_ID.authorization.json"
"$PY" "$AUTH_BUILDER" dispatch \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage DICOM_EXTRACTION --task-scope "$N" --output "$EXTRACTION_DISPATCH_AUTH"
"$PY" "$AUTH_BUILDER" scientific-stage \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage DICOM_EXTRACTION --batch-id "$BATCH_ID" --output "$EXTRACTION_AUTH"
EXTRACTION_JOB="$(dispatch_one --submit DICOM_EXTRACTION \
  "$EXECUTION_ENV" "$LAUNCH_AUTHORITY" "$EXTRACTION_DISPATCH_AUTH" - "$N")"
```

Only after that batch's extraction completes, its evidence independently
passes, and the owner separately authorizes EchoPrime inference may this next
generic one-batch block be used.

```bash
set -euo pipefail
umask 077

AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
PY='/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python'
DISPATCHER="$AUTHORITY_WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh"
EXECUTION_ENV='<OWNER_PRIVATE_MODE_600_EXECUTION_ENV>'
LAUNCH_AUTHORITY='<OWNER_PRIVATE_MODE_600_FINAL_LAUNCH_AUTHORITY>'
AUTH_BUILDER="$AUTHORITY_WORKTREE/scripts/build_lvef_c3_owner_authorization_receipt.py"
ATTEMPT_ROOT='<OWNER_PRIVATE_ATTEMPT_ROOT_ON_PROJECTNB>'
DISPATCH_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/dispatch"
ECHOPRIME_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/echoprime"
OWNER_DATE='<YYYY-MM-DD>'
N='<ONE_COMPLETED_BATCH_TASK_NUMBER_1_TO_19>'
printf -v BATCH_ID 'c3_batch_%03d' "$((N - 1))"

dispatch_one() {
  local job_id
  job_id="$("$DISPATCHER" "$@")"
  test "$(printf '%s\n' "$job_id" | awk '/^[0-9]+([.][0-9-]+:[0-9]+)?$/ {count += 1} END {print count + 0}')" -eq 1
  printf '%s\n' "$job_id"
}

ECHOPRIME_DISPATCH_AUTH="$DISPATCH_AUTH_ROOT/ECHOPRIME_EMBEDDING.$N.dispatch_authorization.json"
ECHOPRIME_AUTH="$ECHOPRIME_AUTH_ROOT/$BATCH_ID.authorization.json"
"$PY" "$AUTH_BUILDER" dispatch \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage ECHOPRIME_EMBEDDING --task-scope "$N" --output "$ECHOPRIME_DISPATCH_AUTH"
"$PY" "$AUTH_BUILDER" scientific-stage \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage ECHOPRIME_EMBEDDING --batch-id "$BATCH_ID" --output "$ECHOPRIME_AUTH"
ECHOPRIME_JOB="$(dispatch_one --submit ECHOPRIME_EMBEDDING \
  "$EXECUTION_ENV" "$LAUNCH_AUTHORITY" "$ECHOPRIME_DISPATCH_AUTH" - "$N")"
```

Only after that batch's EchoPrime inference completes, its embedding and
pooling evidence independently passes, and the owner separately authorizes
preservation may this next generic one-batch block be used.

```bash
set -euo pipefail
umask 077

AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
PY='/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python'
DISPATCHER="$AUTHORITY_WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh"
EXECUTION_ENV='<OWNER_PRIVATE_MODE_600_EXECUTION_ENV>'
LAUNCH_AUTHORITY='<OWNER_PRIVATE_MODE_600_FINAL_LAUNCH_AUTHORITY>'
AUTH_BUILDER="$AUTHORITY_WORKTREE/scripts/build_lvef_c3_owner_authorization_receipt.py"
ATTEMPT_ROOT='<OWNER_PRIVATE_ATTEMPT_ROOT_ON_PROJECTNB>'
DISPATCH_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/dispatch"
PRESERVATION_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/preservation"
OWNER_DATE='<YYYY-MM-DD>'
N='<ONE_COMPLETED_BATCH_TASK_NUMBER_1_TO_19>'
printf -v BATCH_ID 'c3_batch_%03d' "$((N - 1))"

dispatch_one() {
  local job_id
  job_id="$("$DISPATCHER" "$@")"
  test "$(printf '%s\n' "$job_id" | awk '/^[0-9]+([.][0-9-]+:[0-9]+)?$/ {count += 1} END {print count + 0}')" -eq 1
  printf '%s\n' "$job_id"
}

PRESERVATION_DISPATCH_AUTH="$DISPATCH_AUTH_ROOT/BATCH_PRESERVATION.$N.dispatch_authorization.json"
PRESERVATION_AUTH="$PRESERVATION_AUTH_ROOT/$BATCH_ID.authorization.json"
"$PY" "$AUTH_BUILDER" dispatch \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage BATCH_PRESERVATION --task-scope "$N" --output "$PRESERVATION_DISPATCH_AUTH"
"$PY" "$AUTH_BUILDER" scientific-stage \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage BATCH_PRESERVATION --batch-id "$BATCH_ID" --output "$PRESERVATION_AUTH"
PRESERVATION_JOB="$(dispatch_one --submit BATCH_PRESERVATION \
  "$EXECUTION_ENV" "$LAUNCH_AUTHORITY" "$PRESERVATION_DISPATCH_AUTH" - "$N")"
```

For every batch, stop after the preceding preservation submission. Only after
that batch's preservation job completes, its evidence independently passes,
and the owner grants a new cache-retirement authorization may the next generic
one-batch block be used.

```bash
set -euo pipefail
umask 077

AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
PY='/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python'
DISPATCHER="$AUTHORITY_WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh"
EXECUTION_ENV='<OWNER_PRIVATE_MODE_600_EXECUTION_ENV>'
LAUNCH_AUTHORITY='<OWNER_PRIVATE_MODE_600_FINAL_LAUNCH_AUTHORITY>'
AUTH_BUILDER="$AUTHORITY_WORKTREE/scripts/build_lvef_c3_owner_authorization_receipt.py"
ATTEMPT_ROOT='<OWNER_PRIVATE_ATTEMPT_ROOT_ON_PROJECTNB>'
DISPATCH_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/dispatch"
CACHE_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/cache_retirement"
OWNER_DATE='<YYYY-MM-DD>'

dispatch_one() {
  local job_id
  job_id="$("$DISPATCHER" "$@")"
  test "$(printf '%s\n' "$job_id" | awk '/^[0-9]+([.][0-9-]+:[0-9]+)?$/ {count += 1} END {print count + 0}')" -eq 1
  printf '%s\n' "$job_id"
}

N='<ONE_COMPLETED_BATCH_TASK_NUMBER_1_TO_19>'
printf -v BATCH_ID 'c3_batch_%03d' "$((N - 1))"
CACHE_RETIREMENT_DISPATCH_AUTH="$DISPATCH_AUTH_ROOT/CACHE_RETIREMENT.$N.dispatch_authorization.json"
CACHE_RETIREMENT_AUTH="$CACHE_AUTH_ROOT/$BATCH_ID.authorization.json"
"$PY" "$AUTH_BUILDER" dispatch \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage CACHE_RETIREMENT --task-scope "$N" --output "$CACHE_RETIREMENT_DISPATCH_AUTH"
"$PY" "$AUTH_BUILDER" cache-retirement \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --owner-authorization-date-utc '<UTC_OWNER_AUTHORIZATION_Z>' \
  --batch-id "$BATCH_ID" \
  --final-ledger "$ATTEMPT_ROOT/batches/$BATCH_ID/cache_retirement_eligible_resume_ledger.restricted.json" \
  --preservation-receipt "$ATTEMPT_ROOT/batches/$BATCH_ID/preservation/batch_preservation_receipt.restricted.json" \
  --output "$CACHE_RETIREMENT_AUTH"
CACHE_RETIREMENT_JOB="$(dispatch_one --submit CACHE_RETIREMENT \
  "$EXECUTION_ENV" "$LAUNCH_AUTHORITY" "$CACHE_RETIREMENT_DISPATCH_AUTH" - "$N")"
```

Run the finalization block only after all 19 batch-finalization receipts
independently pass.

```bash
set -euo pipefail
umask 077

AUTHORITY_WORKTREE='/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask'
PY='/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python'
DISPATCHER="$AUTHORITY_WORKTREE/scripts/scc_dispatch_lvef_c3_production_v2.sh"
EXECUTION_ENV='<OWNER_PRIVATE_MODE_600_EXECUTION_ENV>'
LAUNCH_AUTHORITY='<OWNER_PRIVATE_MODE_600_FINAL_LAUNCH_AUTHORITY>'
AUTH_BUILDER="$AUTHORITY_WORKTREE/scripts/build_lvef_c3_owner_authorization_receipt.py"
ATTEMPT_ROOT='<OWNER_PRIVATE_ATTEMPT_ROOT_ON_PROJECTNB>'
DISPATCH_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/dispatch"
FINALIZATION_AUTH_ROOT="$ATTEMPT_ROOT/authorizations/finalization"
OWNER_DATE='<YYYY-MM-DD>'

dispatch_one() {
  local job_id
  job_id="$("$DISPATCHER" "$@")"
  test "$(printf '%s\n' "$job_id" | awk '/^[0-9]+([.][0-9-]+:[0-9]+)?$/ {count += 1} END {print count + 0}')" -eq 1
  printf '%s\n' "$job_id"
}

FINALIZATION_DISPATCH_AUTH="$DISPATCH_AUTH_ROOT/PRESERVATION_FINALIZATION.none.dispatch_authorization.json"
FINALIZATION_AUTH="$FINALIZATION_AUTH_ROOT/all_batches.authorization.json"
"$PY" "$AUTH_BUILDER" dispatch \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage PRESERVATION_FINALIZATION --task-scope none \
  --output "$FINALIZATION_DISPATCH_AUTH"
"$PY" "$AUTH_BUILDER" scientific-stage \
  --execution-environment "$EXECUTION_ENV" --launch-authority "$LAUNCH_AUTHORITY" \
  --owner-authorization-affirmed YES --owner-authorization-date "$OWNER_DATE" \
  --stage PRESERVATION_FINALIZATION --output "$FINALIZATION_AUTH"
FINALIZATION_JOB="$(dispatch_one --submit PRESERVATION_FINALIZATION \
  "$EXECUTION_ENV" "$LAUNCH_AUTHORITY" "$FINALIZATION_DISPATCH_AUTH" - none)"
```

The command blocks above are a future authorization packet, not an instruction
to execute. A new commit, changed contract/config/checkpoint/environment,
changed source or batch plan, failed capacity gate, failed stage receipt, or
concurrent batch lock requires fail-closed review. Model-fitting and
confirmatory-access commands are intentionally absent.
