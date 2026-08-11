# C3 control-authority backup and recovery

Status: **implemented offline and owner-gated**.

This authority protects the bounded control plane needed to reconstruct the
prospective selected-cohort C3 run. It does not authorize or perform a cloud
request, object listing, DICOM download, scheduler submission, DICOM decode,
extraction, EchoPrime inference, embedding generation, model fitting,
prediction, or confirmatory-performance access.

## Recovery classes

Every declared item receives exactly one closed classification:

| Classification | Recovery treatment |
|---|---|
| `GIT_ORIGIN_PROTECTED` | Verify the approved origin ref and also create a bounded branch bundle for the isolated restore test. |
| `COMMITTED_RECONSTRUCTABLE` | Recover from the exact governing Git commit. |
| `PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE` | Recreate only from a pinned source and checksum authority. |
| `OWNER_RECREATABLE` | Recreate interactively; never back up its private state. |
| `CHECKSUM_ONLY_NO_COPY_REQUIRED` | Verify and record its current checksum without copying it. This class alone makes no claim that the bytes can be deterministically reacquired after loss. |
| `IRREPLACEABLE_BACKUP_REQUIRED` | Copy into the owner-private backed-tier pack and verify through an isolated second copy. |
| `EXCLUDED_CREDENTIAL_MATERIAL` | Explicitly omit from the backup and restore. |
| `UNRESOLVED` | Fail closed. |

The committed policy fixes the exact declaration set, artifact-role allowlist,
per-role classification and destination, suffix constraints, and byte/file
bounds. Unknown, duplicate, missing, reclassified, or unresolved roles fail.

## Backup boundary

The restricted backup contains only:

- one exact-branch Git bundle;
- the pinned EchoPrime checkpoint;
- the selected-study, selected-source, source-metadata, and split authorities;
- the current-commit production environment receipt, captured before the
  primary backup/restore witness;
- current capacity and historical migration authorities;
- the prior production authority packet;
- the twelve already approved aggregate authorities.

The deterministic batch plan and the EchoPrime environment are checksum-bound
but not copied. Committed code and configuration are recovered from the exact
Git authority, and the pinned Cloud SDK runtime has a separate external-source
reconstruction authority. The live EchoPrime runtime and its exact executable,
package, and environment-receipt hashes are validated for the prospective
canary, but deterministic reacquisition of that environment after loss has
**not** been established. The checksum-only classification deliberately does
not overclaim recoverability: loss of the live environment would require a new
environment rebuild and equivalence authority before EchoPrime inference.

The pack excludes OAuth and ADC state, tokens, Cloud SDK credential databases,
private Google project/billing values, requester-pays/session/preflight files,
service-account keys, unrestricted logs, raw DICOMs, extracted arrays,
embeddings, predictions, and patient-level scientific outputs other than the
already approved restricted manifests named by the allowlist. The checkpoint
is the sole permitted model-binary role.

The approved selected-study, selected-source, source-metadata, and split
manifests are restricted control authorities and may contain identifiers or
locators. They remain owner-private and are not aggregate exports. Accordingly,
the safety attestations are
`approved_restricted_control_authorities_only=true` and
`unapproved_bulk_scientific_payload_absent=true`; they do not claim that every
restricted scientific datum is absent from the recovery pack.

Inputs must be regular owner-owned files below an approved restricted source
root, owner-readable, and not group- or other-writable. Immutable
owner-controlled modes such as 0400, 0600, 0640, and 0644 are accepted without
changing the source. Symlinks, symlinked ancestors, special files, unsafe
relative paths, changed files, and group/other-writable inputs fail closed.
Every created file is mode 0600; private directories may be mode 0700 or the
SCC setgid-private mode 02700. The backup root and restore root must be new,
nonoverlapping, owner-private directories.
Files are published no-clobber with a same-directory temporary inode and an
atomic hard link.

## Isolated recovery test

The tool verifies the live checkout branch, commit, tracked cleanliness,
approved origin URL class, and matching remote-tracking ref. It creates a
branch-scoped bundle, verifies it, clones an isolated bare/common repository,
adds a linked worktree at the exact branch and commit, runs `git fsck --full
--strict`, and compares a complete tracked-file size/SHA-256 set with the live
checkout. Before attesting credential exclusion, it scans every reachable
commit payload, historical pathname, and unique blob in the bundle against the
closed credential/private-cloud patterns. Explicit synthetic-fixture matches
are counted separately. After permission normalization it reruns Git fsck,
branch/commit/status checks, and the complete tracked-file authority. Existing
untracked files are ignored and untouched.

Every non-Git role is bound before either output root is created to a caller-
supplied expected SHA-256 and exact byte size plus a policy-fixed schema kind.
The fixed checkpoint and cohort hashes must also match policy. Strict JSON
rejects duplicate keys; strict CSV rejects duplicate columns and malformed row
widths; the selected-cohort schemas enforce frozen row and source-byte totals.

Each copied non-Git authority is restored into a new isolated research-tier
root. The verifier requires the exact manifest file set, byte sizes, SHA-256
values, owner-private modes, no symlinks, no special files, and a second
credential/private-cloud-value scan. It then rechecks the live checkout and
remote-tracking authority to prove the test did not mutate the live Git state.

The restore root is deliberately retained as restricted evidence. It is not a
production root and cannot become execution authority merely by existing.

The restore witness proves recovery of the copied control authorities and
checksum agreement with the currently available EchoPrime environment receipt.
It does not restore the EchoPrime virtual environment itself and does not prove
that an independently rebuilt environment would be byte-identical. This
limitation does not authorize inference and cannot be converted into an
environment-recovery claim by the presence of a checksum alone.

## Terminal current-chain recovery seal

The primary backup/restore witness must contain the current-commit environment,
not only its predecessor. The current packet, launch envelope, exact future
command, and final aggregate are created later and contain timestamps; a hash
record under the non-backed research tier does not make their exact bytes
recoverable. After those artifacts exist, a separate no-clobber owner-private
terminal seal must copy the exact current chain to the backed tier, record
safe-relative paths, sizes, and SHA-256 values, and verify a second isolated
read without altering the primary backup or live worktrees. The terminal seal
may bind the earlier backup manifest and restore receipt, but neither may be
rewritten to avoid a checksum cycle.

If that terminal seal is absent or fails, `CURRENT_COMMIT_PACKET` remains
failed even when the research-tier packet and launch envelope validate. The
seal grants zero cloud, scheduler, transfer, or model scope.

## Restricted and aggregate artifacts

The backup root contains:

- `backup_manifest.restricted.json`, artifact type
  `lvef_c3_control_authority_backup_manifest_v1`;
- `restore_receipt.restricted.json`, artifact type
  `lvef_c3_control_authority_restore_receipt_v1`;
- the bounded Git bundle and allowlisted copied files.

The separately specified aggregate output has artifact type
`lvef_c3_control_authority_backup_recovery_summary_v1`. It contains only
counts, byte totals, hashes, pass/fail booleans, the governing commit, and zero-
execution attestations. It contains no source or destination paths, identifiers,
credentials, project values, or file contents.

## Exact invocation interface

The following is a template only. It is **UNEXECUTED** and all path, expected-
SHA-256, and expected-byte-size variables must be supplied inside the owner-
private SCC attempt wrapper. Every artifact argument has the closed form
`ROLE=CLASS=EXPECTED_SHA256=EXPECTED_SIZE=POLICY_SCHEMA=PATH`. The command has
no network implementation.

```bash
# UNEXECUTED — OFFLINE CONTROL-AUTHORITY BACKUP/RESTORE ONLY
"$PYTHON" scripts/build_lvef_c3_backup_recovery_witness.py \
  --policy configs/lvef_c3_backup_recovery_policy_v1.yaml \
  --attempt-id "$ATTEMPT_ID" \
  --governing-commit "$GOVERNING_COMMIT" \
  --checkout "$CANONICAL_CHECKOUT" \
  --declaration git_repository=GIT_ORIGIN_PROTECTED \
  --declaration tracked_code_and_configuration=COMMITTED_RECONSTRUCTABLE \
  --declaration echoprime_environment=CHECKSUM_ONLY_NO_COPY_REQUIRED \
  --declaration cloudsdk_runtime=PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE \
  --declaration owner_interactive_authentication=OWNER_RECREATABLE \
  --declaration cloud_credentials=EXCLUDED_CREDENTIAL_MATERIAL \
  --declaration requester_pays_private_environment=EXCLUDED_CREDENTIAL_MATERIAL \
  --artifact "checkpoint=IRREPLACEABLE_BACKUP_REQUIRED=$CHECKPOINT_SHA256=$CHECKPOINT_SIZE=echoprime_checkpoint_v1=$CHECKPOINT" \
  --artifact "selected_study_manifest=IRREPLACEABLE_BACKUP_REQUIRED=$SELECTED_STUDIES_SHA256=$SELECTED_STUDIES_SIZE=selected_study_manifest_v1=$SELECTED_STUDIES" \
  --artifact "selected_source_manifest=IRREPLACEABLE_BACKUP_REQUIRED=$SELECTED_SOURCE_SHA256=$SELECTED_SOURCE_SIZE=selected_source_manifest_v1=$SELECTED_SOURCE" \
  --artifact "selected_source_metadata_receipt=IRREPLACEABLE_BACKUP_REQUIRED=$SOURCE_METADATA_SHA256=$SOURCE_METADATA_SIZE=selected_source_metadata_receipt_v1=$SOURCE_METADATA" \
  --artifact "split_map=IRREPLACEABLE_BACKUP_REQUIRED=$SPLIT_MAP_SHA256=$SPLIT_MAP_SIZE=subject_split_map_v1=$SPLIT_MAP" \
  --artifact "production_environment_receipt=IRREPLACEABLE_BACKUP_REQUIRED=$ENVIRONMENT_RECEIPT_SHA256=$ENVIRONMENT_RECEIPT_SIZE=strict_json_mapping_v1=$ENVIRONMENT_RECEIPT" \
  --artifact "post_reallocation_capacity_receipt=IRREPLACEABLE_BACKUP_REQUIRED=$CAPACITY_RECEIPT_SHA256=$CAPACITY_RECEIPT_SIZE=strict_json_mapping_v1=$CAPACITY_RECEIPT" \
  --artifact "post_reallocation_capacity_aggregate=IRREPLACEABLE_BACKUP_REQUIRED=$CAPACITY_AGGREGATE_SHA256=$CAPACITY_AGGREGATE_SIZE=strict_json_mapping_v1=$CAPACITY_AGGREGATE" \
  --artifact "historical_migration_witness=IRREPLACEABLE_BACKUP_REQUIRED=$MIGRATION_WITNESS_SHA256=$MIGRATION_WITNESS_SIZE=strict_json_mapping_v1=$MIGRATION_WITNESS" \
  --artifact "historical_migration_classification=IRREPLACEABLE_BACKUP_REQUIRED=$MIGRATION_CLASSIFICATION_SHA256=$MIGRATION_CLASSIFICATION_SIZE=strict_json_mapping_v1=$MIGRATION_CLASSIFICATION" \
  --artifact "prior_production_authority_packet=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_PACKET_SHA256=$PRIOR_PACKET_SIZE=strict_json_mapping_v1=$PRIOR_PACKET" \
  --artifact "production_batch_plan=CHECKSUM_ONLY_NO_COPY_REQUIRED=$BATCH_PLAN_SHA256=$BATCH_PLAN_SIZE=strict_json_mapping_v1=$BATCH_PLAN" \
  --artifact "prior_safe_aggregate_01=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_01_SHA256=$PRIOR_SAFE_01_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_01" \
  --artifact "prior_safe_aggregate_02=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_02_SHA256=$PRIOR_SAFE_02_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_02" \
  --artifact "prior_safe_aggregate_03=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_03_SHA256=$PRIOR_SAFE_03_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_03" \
  --artifact "prior_safe_aggregate_04=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_04_SHA256=$PRIOR_SAFE_04_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_04" \
  --artifact "prior_safe_aggregate_05=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_05_SHA256=$PRIOR_SAFE_05_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_05" \
  --artifact "prior_safe_aggregate_06=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_06_SHA256=$PRIOR_SAFE_06_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_06" \
  --artifact "prior_safe_aggregate_07=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_07_SHA256=$PRIOR_SAFE_07_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_07" \
  --artifact "prior_safe_aggregate_08=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_08_SHA256=$PRIOR_SAFE_08_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_08" \
  --artifact "prior_safe_aggregate_09=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_09_SHA256=$PRIOR_SAFE_09_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_09" \
  --artifact "prior_safe_aggregate_10=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_10_SHA256=$PRIOR_SAFE_10_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_10" \
  --artifact "prior_safe_aggregate_11=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_11_SHA256=$PRIOR_SAFE_11_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_11" \
  --artifact "prior_safe_aggregate_12=IRREPLACEABLE_BACKUP_REQUIRED=$PRIOR_SAFE_12_SHA256=$PRIOR_SAFE_12_SIZE=strict_json_or_csv_v1=$PRIOR_SAFE_12" \
  --backup-root "$BACKUP_ROOT" \
  --restore-root "$RESTORE_ROOT" \
  --aggregate-output "$AGGREGATE_OUTPUT"
```

Successful primary creation proves recovery only for the artifacts it lists.
Current-chain recovery additionally requires the terminal seal above. Neither
artifact grants production or model scope or authorizes the first DICOM-body
request.
