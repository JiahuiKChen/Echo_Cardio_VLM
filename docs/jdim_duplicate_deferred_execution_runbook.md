# Deferred JDIM Duplicate-Correction Execution Bundle

This bundle is prepared only. No command in this document was submitted during
Phase 2D. `NO_QSUB_MODE=TRUE` remains controlling until the author explicitly
authorizes new SCC jobs.

The current metadata result is already
`DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE`: 32 groups are
`TRUE_DUPLICATE_EXPECTED_ROWS`. Therefore Jobs A and B are inactive contingency
jobs. Job C is the first scientifically required scheduled stage after renewed
authorization.

## Shared environment

Run only after explicit author authorization and after confirming that no listed
output root exists.

```bash
export NO_QSUB_MODE=FALSE
export JDIM_SGE_PROJECT=mimicecho
export JDIM_REPO_ROOT=/restricted/project/mimicecho/code/Echo_Cardio_VLM_jdim_noqsub_provenance_prep
export JDIM_CANONICAL_CODE_ROOT=/restricted/project/mimicecho/code/Echo_Cardio_VLM
export JDIM_PYTHON_BIN="$JDIM_CANONICAL_CODE_ROOT/.venv-echoprime/bin/python"
export JDIM_FULLSCALE_ROOT="$JDIM_CANONICAL_CODE_ROOT/outputs/cloud_cohorts/fullscale_all"
export JDIM_LEGACY_ROOT="$JDIM_CANONICAL_CODE_ROOT/outputs/cloud_cohorts/stage_d_500study_scc"
export JDIM_LINEAGE_REPAIR_ROOT=/restricted/project/mimicecho/outputs/jdim_major_revision_lineage_repair_v1
export JDIM_METADATA_ROOT=/restricted/project/mimicecho/outputs/jdim_duplicate_metadata_only_v1
export JDIM_DUPLICATE_DECISION_ROOT=/restricted/project/mimicecho/outputs/jdim_duplicate_resolution_v1/duplicate_forensics
export JDIM_OUTPUT_ROOT=/restricted/project/mimicecho/outputs/jdim_major_revision_repaired_v2
export JDIM_CORRECTED_ROOT=/restricted/project/mimicecho/outputs/jdim_major_revision_duplicate_corrected_v2
export JDIM_PHASE2_ROOT=/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2
export JDIM_SOURCE_STUDIES_CSV="$JDIM_LINEAGE_REPAIR_ROOT/restricted/lineage/mimic_iv_echo_source_studies_v1.csv"
export JDIM_LINEAGE_JSON="$JDIM_LINEAGE_REPAIR_ROOT/restricted/lineage/jdim_cohort_lineage_v1.json"
export JDIM_SELECTED_STUDIES_CSV="$JDIM_FULLSCALE_ROOT/manifests/all_eligible_studies.csv"
export JDIM_STRUCTURED_MEASUREMENTS_CSV="$JDIM_FULLSCALE_ROOT/manifests/structured_measurements.csv"
export JDIM_SPLIT_MAP_CSV="$JDIM_FULLSCALE_ROOT/manifests/subject_split_map_v1.csv"
export JDIM_STUDY_EMBEDDING_NPZ="$JDIM_FULLSCALE_ROOT/study_embeddings_512/study_embeddings_512.npz"
export JDIM_STUDY_EMBEDDING_MANIFEST_CSV="$JDIM_FULLSCALE_ROOT/study_embeddings_512/study_embedding_manifest.csv"
export JDIM_CLIP_EMBEDDING_NPZ="$JDIM_FULLSCALE_ROOT/merged_clip_embeddings_512/clip_embeddings_512.npz"
export JDIM_CLIP_EMBEDDING_MANIFEST_CSV="$JDIM_FULLSCALE_ROOT/merged_clip_embeddings_512/clip_embedding_manifest.csv"
export JDIM_ENCODER_CHECKPOINT=/restricted/project/mimicecho/echoprime_weights/echo_prime_encoder.pt
export JDIM_LVOT_SUMMARY_JSON="$JDIM_PHASE2_ROOT/lvot_vti/all_clips/imaging_baseline_summary.json"
export JDIM_TAPSE_SUMMARY_JSON="$JDIM_PHASE2_ROOT/tapse/all_clips/imaging_baseline_summary.json"
export JDIM_LVOT_PREDICTIONS_CSV="$JDIM_PHASE2_ROOT/lvot_vti/all_clips/imaging_baseline_predictions.csv"
export JDIM_TAPSE_PREDICTIONS_CSV="$JDIM_PHASE2_ROOT/tapse/all_clips/imaging_baseline_predictions.csv"
export JDIM_AUDIT_CONFIG="$JDIM_REPO_ROOT/configs/jdim_input_content_audit_v1.yaml"
export JDIM_RESTRICTED_AUDIT_ROOT="$JDIM_OUTPUT_ROOT/restricted/input_content_audit"
export JDIM_AUDIT_KEY_FILE="$JDIM_OUTPUT_ROOT/restricted/keys/jdim_audit_hmac_key.bin"
export JDIM_BOOTSTRAP_N=2000
export JDIM_PHASE2_RANDOM_SEED=1337
export JDIM_PHASE2_RIDGE_ALPHAS=0.01,0.03,0.1,0.3,1,3,10,30,100,300,1000
```

Before submission:

```bash
cd "$JDIM_REPO_ROOT"
git pull --ff-only
git status --short
git rev-parse HEAD
test ! -e "$JDIM_OUTPUT_ROOT"
test ! -e "$JDIM_CORRECTED_ROOT"
mkdir -p "$JDIM_OUTPUT_ROOT/logs" "$JDIM_OUTPUT_ROOT/restricted/keys"
```

The pulled SHA must equal the final remote SHA reported for
`codex/jdim-noqsub-provenance-prep`, and `git status --short` must be empty.

## Job A: targeted recovery contingency

Entry condition: submit only if a future metadata result reports one or more
`REQUIRES_REHYDRATION` groups. The current entry condition is false.

Prepared output root:

`/restricted/project/mimicecho/outputs/jdim_duplicate_recovery_v1`

Prepared command:

```bash
qsub -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_dup_recover -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 \
  -l mem_per_core=16G -l gpus=1 -l gpu_c=8.0 -l gpu_memory=48G -b y \
  "$JDIM_PYTHON_BIN" "$JDIM_REPO_ROOT/scripts/recover_jdim_duplicate_inputs.py" \
  --recovery-manifest-csv /restricted/project/mimicecho/outputs/jdim_duplicate_recovery_v1/restricted/recovery_manifest.csv \
  --historical-embedding-npz "$JDIM_FULLSCALE_ROOT/batches/batch_000_embeddings/clip_embeddings_512.npz" \
  --weights-dir /restricted/project/mimicecho/echoprime_weights \
  --checkpoint-sha256 "[AUTHORIZE_AND_INSERT_PINNED_SHA256]" \
  --extraction-script "$JDIM_REPO_ROOT/scripts/extract_mimic_echo_cines.py" \
  --extraction-script-sha256 "[INSERT_SHA256_FROM_RESTRICTED_METADATA_PROVENANCE]" \
  --embedding-script "$JDIM_REPO_ROOT/scripts/extract_echoprime_embeddings.py" \
  --embedding-script-sha256 "[INSERT_SHA256_FROM_RESTRICTED_METADATA_PROVENANCE]" \
  --output-root /restricted/project/mimicecho/outputs/jdim_duplicate_recovery_v1/run \
  --device cuda
```

- Expected runtime: under 4 hours; only affected inputs.
- Restricted outputs: recovered inputs, source/processed hashes, per-group vector
  comparisons.
- Safe outputs: status counts and restricted-packet hashes only.
- Success: all groups report `DUPLICATE_SEMANTICS_RESOLVED`.
- Failure: any `BLOCKED_*`, `RECONSTRUCTED_VECTOR_MISMATCH`, or unresolved group.

## Job B: duplicate forensics rerun contingency

Entry condition: Job A completed and every recovered group resolved. The current
entry condition is false because Job A is unnecessary.

```bash
qsub -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_dup_reclass -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=01:00:00 -pe omp 1 \
  -l mem_per_core=4G -b y \
  "$JDIM_PYTHON_BIN" "$JDIM_REPO_ROOT/scripts/resolve_jdim_duplicate_provenance.py" \
  --prior-forensics-rows "$JDIM_LINEAGE_REPAIR_ROOT/restricted/duplicate_forensics/duplicate_forensics_rows.csv" \
  --prior-forensics-summary "$JDIM_LINEAGE_REPAIR_ROOT/aggregate_safe/duplicate_forensics/duplicate_forensics_summary.json" \
  --metadata-groups-csv "$JDIM_METADATA_ROOT/restricted/metadata_group_classification.csv" \
  --metadata-summary-json "$JDIM_METADATA_ROOT/aggregate_safe/duplicate_metadata_summary.json" \
  --recovery-rows /restricted/project/mimicecho/outputs/jdim_duplicate_recovery_v1/run/restricted/duplicate_recovery_rows.csv \
  --recovery-summary /restricted/project/mimicecho/outputs/jdim_duplicate_recovery_v1/run/aggregate_safe/duplicate_recovery_summary.json \
  --output-root /restricted/project/mimicecho/outputs/jdim_duplicate_resolution_rehydrated_v1/duplicate_forensics
```

- Expected runtime: under 15 minutes, metadata only.
- Dependency: Job A successful; submit manually after inspecting Job A rather
  than using a dependency on either protected LVEF job.
- Success: 32 hash-bound resolved decision groups and exactly one deterministic
  keeper per group.
- Failure: any packet-hash mismatch, incomplete group set, or unresolved status.

## Job C: corrected aggregation and impact analysis

Entry condition: the decision summary reports `status=ok`, 32
`TRUE_DUPLICATE_EXPECTED_ROWS`, zero ambiguity, and a matching restricted-row
hash. This condition is currently satisfied. Use only new-job dependencies.

If the original fixed-prediction metrics directory is absent, first submit:

```bash
ORIGINAL_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" \
  -N jdim_fixed_original -j y -o "$JDIM_OUTPUT_ROOT/logs" \
  -l h_rt=04:00:00 -pe omp 2 -l mem_per_core=8G -b y \
  "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" reviewer-metrics)
```

Otherwise set `ORIGINAL_JOB` to the empty string and verify the existing
lineage-repair reviewer-metrics packet before continuing.

```bash
AGG_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" \
  -N jdim_correct_agg -j y -o "$JDIM_OUTPUT_ROOT/logs" \
  -l h_rt=04:00:00 -pe omp 2 -l mem_per_core=16G -b y \
  "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" corrected-aggregation)

ANALYSIS_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" \
  -hold_jid "$AGG_JOB" -N jdim_correct_models -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=12:00:00 -pe omp 4 \
  -l mem_per_core=16G -b y \
  "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" corrected-analysis)
```

When `ORIGINAL_JOB` is nonempty:

```bash
COMPARE_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" \
  -hold_jid "$ANALYSIS_JOB,$ORIGINAL_JOB" -N jdim_correct_compare -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 \
  -l mem_per_core=8G -b y \
  "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" corrected-comparison)
```

When the verified original metrics already exist:

```bash
COMPARE_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" \
  -hold_jid "$ANALYSIS_JOB" -N jdim_correct_compare -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 \
  -l mem_per_core=8G -b y \
  "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" corrected-comparison)
```

- Expected runtime: aggregation under 4 hours; analyses under 12 hours;
  comparison under 4 hours.
- Restricted outputs: corrected clip/study stores, predictions, input
  provenance, and per-study impact.
- Safe outputs: aggregate impact, corrected metrics, and completion certificate.
- Success: `corrected_analysis_completion_v1.json` validates all required
  artifacts and unchanged protocol fields.
- Failure: any replay mismatch, row/split/protocol mismatch, divergent duplicate
  vectors, missing completion certificate, or existing output collision.

## Job D: repaired cohort flow

Entry condition: Job C comparison completed successfully and its completion
certificate exists.

```bash
export JDIM_STUDY_EMBEDDING_NPZ="$JDIM_CORRECTED_ROOT/aggregation/restricted/corrected_study_embeddings.npz"
export JDIM_STUDY_EMBEDDING_MANIFEST_CSV="$JDIM_CORRECTED_ROOT/aggregation/restricted/corrected_study_embedding_manifest.csv"
export JDIM_CLIP_EMBEDDING_NPZ="$JDIM_CORRECTED_ROOT/aggregation/restricted/deduplicated_clip_embeddings.npz"
export JDIM_CLIP_EMBEDDING_MANIFEST_CSV="$JDIM_CORRECTED_ROOT/aggregation/restricted/deduplicated_clip_manifest.csv"
export JDIM_LVOT_SUMMARY_JSON="$JDIM_CORRECTED_ROOT/restricted/analyses/lvot_vti/all_clips/imaging_baseline_summary.json"
export JDIM_TAPSE_SUMMARY_JSON="$JDIM_CORRECTED_ROOT/restricted/analyses/tapse/all_clips/imaging_baseline_summary.json"
export JDIM_LVOT_PREDICTIONS_CSV="$JDIM_CORRECTED_ROOT/restricted/analyses/lvot_vti/all_clips/imaging_baseline_predictions.csv"
export JDIM_TAPSE_PREDICTIONS_CSV="$JDIM_CORRECTED_ROOT/restricted/analyses/tapse/all_clips/imaging_baseline_predictions.csv"

COHORT_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" \
  -hold_jid "$COMPARE_JOB" -N jdim_cohort_repaired -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 \
  -l mem_per_core=8G -b y \
  "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" cohort-flow)
```

- Expected runtime: under 4 hours.
- Restricted outputs: reconciliation and target-cohort rows.
- Safe outputs: cohort-flow counts and invariant report.
- Success: all lineage, split, duplicate-decision, embedding, target, and
  prediction invariants pass.
- Failure: any `BLOCKED_*` status or count/hash mismatch.

## Job E: audit sampling and technical pilot

Entry condition: repaired cohort flow passed. Create the opaque key once under
restricted permissions, then submit only new audit jobs.

```bash
(umask 077; openssl rand -out "$JDIM_AUDIT_KEY_FILE" 32)
chmod 600 "$JDIM_AUDIT_KEY_FILE"

PILOT_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" \
  -hold_jid "$COHORT_JOB" -N jdim_audit_pilot -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=01:00:00 -pe omp 1 \
  -l mem_per_core=4G -b y \
  "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" audit-pilot)
```

After the technical pilot manifest passes, set the uniquely confirmed official
DICOM root and submit reconstruction:

```bash
export JDIM_DICOM_DATA_ROOT="[AUTHOR TO CONFIRM UNIQUE OFFICIAL DICOM ROOT]"

RECON_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" \
  -hold_jid "$PILOT_JOB" -N jdim_audit_reconstruct -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 \
  -l mem_per_core=8G -b y \
  "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" audit-reconstruct-pilot)
```

Submit the main sample only after the pilot reconstruction satisfies the locked
failure-rate stop rule:

```bash
qsub -cwd -V -P "$JDIM_SGE_PROJECT" -hold_jid "$RECON_JOB" \
  -N jdim_audit_sample -j y -o "$JDIM_OUTPUT_ROOT/logs" \
  -l h_rt=01:00:00 -pe omp 1 -l mem_per_core=4G -b y \
  "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" audit-sample
```

- Expected runtime: pilot/sample under 1 hour each; reconstruction under 4
  hours.
- Restricted outputs: opaque linkage, canonical clip roster, reconstruction
  packet, and later human annotations.
- Safe outputs: technical completion and aggregate audit summaries only.
- Success: locked sample token, exact roster checks, reconstruction success, and
  no stop-rule violation.
- Failure: nonunique DICOM linkage, reconstruction mismatch, roster mismatch,
  or stop-rule breach.

No command above uses `hold_jid` against jobs `7292691` or `7292692`.

