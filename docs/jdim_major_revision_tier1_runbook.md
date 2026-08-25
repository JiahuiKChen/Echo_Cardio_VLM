# JDIM Major Revision Lineage-Repair SCC Runbook

This runbook executes the lineage repair and frozen-protocol revision tooling
for JDIM-D-26-02840 on SCC. It does not change the subject split, target
definitions, median label aggregation, model family, alpha grid, validation-only
selection, preprocessing, or thresholds. A downstream Ridge refit is permitted
only if restricted forensics confirm true unintended duplicate weighting; the
refit then uses corrected embeddings and the unchanged stable-v2 protocol.
Row-level source files, predictions, audit linkage, annotations, and provenance
specifications must stay under approved restricted storage.

Do not proceed if the checked-out commit is dirty, the frozen Phase-2 artifacts
are missing, the split-map lineage cannot be confirmed, or a cohort invariant
returns `BLOCKED_MIXED_OR_UNRESOLVED_LINEAGE`. Duplicate ambiguity returns
`BLOCKED_DUPLICATE_SEMANTICS_UNRESOLVED` and stops cohort/audit work.

## 1. Create an isolated worktree at the reviewed repair branch

```bash
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
git fetch origin
git worktree add --detach \
  /restricted/project/mimicecho/code/Echo_Cardio_VLM_jdim_lineage_repair \
  origin/codex/jdim-major-revision-lineage-repair
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM_jdim_lineage_repair
git status --short
git rev-parse HEAD
```

`git status --short` must be empty. Do not run the provenance command from a
dirty checkout.

## 2. Load and pin the SCC environment

```bash
module load python3/3.10.12
module load google-cloud-sdk/455.0.0
export JDIM_REPO_ROOT=/restricted/project/mimicecho/code/Echo_Cardio_VLM_jdim_lineage_repair
export JDIM_CANONICAL_CODE_ROOT=/restricted/project/mimicecho/code/Echo_Cardio_VLM
source "$JDIM_CANONICAL_CODE_ROOT/scc_env.sh"

export JDIM_PYTHON_BIN="$JDIM_CANONICAL_CODE_ROOT/.venv-echoprime/bin/python"
export JDIM_FULLSCALE_ROOT="$JDIM_CANONICAL_CODE_ROOT/outputs/cloud_cohorts/fullscale_all"
export JDIM_LEGACY_ROOT="$JDIM_CANONICAL_CODE_ROOT/outputs/cloud_cohorts/stage_d_500study_scc"
export JDIM_OUTPUT_ROOT=/restricted/project/mimicecho/outputs/jdim_major_revision_lineage_repair_v1
export JDIM_PHASE2_ROOT=/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2
export JDIM_SOURCE_STUDIES_CSV="$JDIM_OUTPUT_ROOT/restricted/lineage/mimic_iv_echo_source_studies_v1.csv"
export JDIM_LINEAGE_JSON="$JDIM_OUTPUT_ROOT/restricted/lineage/jdim_cohort_lineage_v1.json"
export JDIM_SELECTED_STUDIES_CSV="$JDIM_FULLSCALE_ROOT/manifests/all_eligible_studies.csv"
export JDIM_STRUCTURED_MEASUREMENTS_CSV="$JDIM_FULLSCALE_ROOT/manifests/structured_measurements.csv"
export JDIM_SPLIT_MAP_CSV="$JDIM_FULLSCALE_ROOT/manifests/subject_split_map_v1.csv"
export JDIM_SPLIT_MAP_SUMMARY_JSON="$JDIM_FULLSCALE_ROOT/manifests/subject_split_map_v1.summary.json"
export JDIM_FROZEN_STUDY_EMBEDDING_NPZ="$JDIM_FULLSCALE_ROOT/study_embeddings_512/study_embeddings_512.npz"
export JDIM_FROZEN_STUDY_EMBEDDING_MANIFEST_CSV="$JDIM_FULLSCALE_ROOT/study_embeddings_512/study_embedding_manifest.csv"
export JDIM_HISTORICAL_CLIP_EMBEDDING_NPZ="$JDIM_FULLSCALE_ROOT/merged_clip_embeddings_512/clip_embeddings_512.npz"
export JDIM_HISTORICAL_CLIP_EMBEDDING_MANIFEST_CSV="$JDIM_FULLSCALE_ROOT/merged_clip_embeddings_512/clip_embedding_manifest.csv"
export JDIM_STUDY_EMBEDDING_NPZ="$JDIM_FROZEN_STUDY_EMBEDDING_NPZ"
export JDIM_STUDY_EMBEDDING_MANIFEST_CSV="$JDIM_FROZEN_STUDY_EMBEDDING_MANIFEST_CSV"
export JDIM_CLIP_EMBEDDING_NPZ="$JDIM_HISTORICAL_CLIP_EMBEDDING_NPZ"
export JDIM_CLIP_EMBEDDING_MANIFEST_CSV="$JDIM_HISTORICAL_CLIP_EMBEDDING_MANIFEST_CSV"
export JDIM_ENCODER_CHECKPOINT=/restricted/project/mimicecho/echoprime_weights/echo_prime_encoder.pt
export JDIM_LVOT_SUMMARY_JSON="$JDIM_PHASE2_ROOT/lvot_vti/all_clips/imaging_baseline_summary.json"
export JDIM_TAPSE_SUMMARY_JSON="$JDIM_PHASE2_ROOT/tapse/all_clips/imaging_baseline_summary.json"
export JDIM_LVOT_PREDICTIONS_CSV="$JDIM_PHASE2_ROOT/lvot_vti/all_clips/imaging_baseline_predictions.csv"
export JDIM_TAPSE_PREDICTIONS_CSV="$JDIM_PHASE2_ROOT/tapse/all_clips/imaging_baseline_predictions.csv"
export JDIM_AUDIT_CONFIG="$JDIM_REPO_ROOT/configs/jdim_input_content_audit_v1.yaml"
export JDIM_RESTRICTED_AUDIT_ROOT="$JDIM_OUTPUT_ROOT/restricted/input_content_audit"
export JDIM_AUDIT_KEY_FILE="$JDIM_OUTPUT_ROOT/restricted/keys/jdim_audit_hmac_key.bin"
export JDIM_CORRECTED_ROOT=/restricted/project/mimicecho/outputs/jdim_major_revision_duplicate_corrected_v1
export JDIM_BOOTSTRAP_N=2000
export JDIM_PHASE2_RANDOM_SEED=1337
export JDIM_PHASE2_RIDGE_ALPHAS=0.01,0.03,0.1,0.3,1,3,10,30,100,300,1000
export JDIM_SGE_PROJECT=mimicecho

mkdir -p "$JDIM_OUTPUT_ROOT/restricted/lineage"
mkdir -p "$JDIM_OUTPUT_ROOT/restricted/keys"
mkdir -p "$JDIM_OUTPUT_ROOT/aggregate_safe"
mkdir -p "$JDIM_OUTPUT_ROOT/logs"

test -x "$JDIM_PYTHON_BIN"
"$JDIM_PYTHON_BIN" -c 'import cv2, numpy, pandas, pydicom, scipy, sklearn; print("tier1_python_ok")'
```

Every canonical input is explicit. If SCC uses another approved location,
change only the corresponding variable and rerun `handoff-check`. Do not fall
back to the isolated worktree or home storage for untracked inputs, environments,
or restricted artifacts.

## 3. Export the official release source denominator

First confirm the accessed MIMIC-IV-ECHO release in the PhysioNet entitlement
and SCC source configuration. Then export one row per DICOM-linked study from
the official `echo_record_list`; this file is restricted because it contains
subject and study identifiers.

```bash
export ECHO_AI_BILLING_PROJECT=mimic-iv-anesthesia
test ! -e "$JDIM_SOURCE_STUDIES_CSV"

bq --project_id="$ECHO_AI_BILLING_PROJECT" query \
  --nouse_legacy_sql \
  --format=csv \
  --max_rows=50000 \
  'SELECT subject_id, study_id, COUNT(*) AS n_dicoms
   FROM `physionet-data.mimiciv_echo.echo_record_list`
   GROUP BY subject_id, study_id
   ORDER BY subject_id, study_id' \
  > "$JDIM_SOURCE_STUDIES_CSV"

test -s "$JDIM_SOURCE_STUDIES_CSV"
head -1 "$JDIM_SOURCE_STUDIES_CSV"
```

The expected header contains `subject_id`, `study_id`, and `n_dicoms`. Do not
copy this CSV into Git or outside restricted storage.

## 4. Pin lineage metadata without changing the split

Inspect the existing frozen split summary and verify that it describes the
already-used split. Do not regenerate the CSV.

```bash
cat "$JDIM_SPLIT_MAP_SUMMARY_JSON"
sha256sum "$JDIM_SPLIT_MAP_CSV"
```

At the pinned repository base, the full-scale fallback generator sorts unique
subjects, derives a NumPy seed from `echo-ai-fixed-split-seed-v1`, shuffles
once, and partitions 0.70/0.15/remainder. If the SCC summary does not confirm
that lineage, stop and replace the generator description below with the actual
confirmed generator; do not infer or recreate it.

```bash
set -euo pipefail
shopt -s nullglob

BATCH_SOURCE_ARGS=(--batch-source legacy_stage_d_500=legacy)
for path in "$JDIM_FULLSCALE_ROOT"/batches/batch_*_embeddings/clip_embedding_manifest.csv; do
  batch_dir="$(basename "$(dirname "$path")")"
  batch_name="${batch_dir%_embeddings}"
  BATCH_SOURCE_ARGS+=(--batch-source "${batch_name}=fullscale")
done

cd "$JDIM_REPO_ROOT"
"$JDIM_PYTHON_BIN" scripts/build_jdim_cohort_lineage_metadata.py \
  --mimic-iv-echo-release "MIMIC-IV-ECHO v1.0" \
  --source-denominator-definition "Distinct subject_id-study_id pairs in physionet-data.mimiciv_echo.echo_record_list" \
  --imaging-lineage "fullscale_all batch clip manifests plus separately reconciled legacy Stage D batch; mean-pooled final study embeddings" \
  --label-lineage "structured measurements exported for the pinned all_eligible_studies universe; canonical target parsing and study-level median aggregation" \
  --split-map-csv "$JDIM_SPLIT_MAP_CSV" \
  --split-version subject_split_map_v1 \
  --split-generator "confirmed fullscale inline fallback: echo-ai-fixed-split-seed-v1 NumPy shuffle; 0.70/0.15/remainder" \
  "${BATCH_SOURCE_ARGS[@]}" \
  --allow-outside-universe-batch legacy_stage_d_500 \
  --output-json "$JDIM_LINEAGE_JSON"

"$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" handoff-check
```

Do not add `--allow-batch-overlap` preemptively. If the cohort tool detects an
overlap, reconcile its cause first. Declare a pair only when the team can
document that the overlap is expected and legitimate.

## 5. Dry-run and schema validation

```bash
"$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" validate
```

This validates the audit configuration and saved-prediction schemas. Before
duplicate forensics exists, cohort schema/hash validation is explicitly
reported as deferred. Rerun `validate` after section 6; at that point it also
requires the forensic decision hash, every batch manifest/embedding-array hash,
the extraction-manifest set, and the pinned split hash to match the forensic
provenance. It does not compute cohort counts or reviewer metrics. A nonzero
exit or any `BLOCKED_*` status stops execution.

## 6. Adjudicate repeated clip keys before cohort or audit work

```bash
qsub -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_dup_forensics -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=08:00:00 -pe omp 2 -l mem_per_core=16G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" duplicate-forensics
```

This job opens processed NPZ arrays only for repeated groups, compares the
frozen vectors, and writes row-level evidence under
`$JDIM_OUTPUT_ROOT/restricted/duplicate_forensics`. Only the parallel
`aggregate_safe/duplicate_forensics` summaries may be inspected outside the
restricted row evidence.

Forensic `_manifest_row` is the zero-based ordinal among successful embedding
rows after applying `write_ok`, not the physical CSV line number. Cohort
reconciliation applies the same definition and verifies the associated study
and subject identity before consuming a decision.

The historical full-scale runner treated source DICOMs and processed NPZs as
scratch artifacts and may have purged them after embedding. If a repeated row's
processed NPZ is absent, the classifier must return ambiguity. Do not infer
physical-input identity from path equality alone. Recover the original
restricted artifact, or rehydrate the official DICOM and reproduce preprocessing
under the pinned historical code/environment with exact frame, processed-array,
prepared-input, and embedding checks before seeking a new adjudication.

Require exit status 0 unless the aggregate summary explicitly reports
`BLOCKED_DUPLICATE_SEMANTICS_UNRESOLVED`. Then follow exactly one branch:

```bash
"$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" validate
```

This second validation binds the restricted decision rows to the aggregate-safe
forensic provenance and to the exact batch inputs before any cohort decision is
consumed.

- All groups `LEGITIMATE_DISTINCT_CLIPS` or `KEY_GRANULARITY_TOO_COARSE`: do not
  deduplicate. Keep the original clip/study embeddings and predictions, refine
  the documented clip key, and proceed to section 8.
- At least one `TRUE_DUPLICATE_MANIFEST_ROWS` or
  `TRUE_DUPLICATE_EMBEDDING_ROWS`, with no ambiguity: execute section 7. The
  corrected analysis becomes canonical regardless of performance direction.
- Any `AMBIGUOUS_REQUIRES_AUTHOR_REVIEW`: stop with
  `BLOCKED_DUPLICATE_SEMANTICS_UNRESOLVED`. Do not run cohort flow, sampling, or
  any corrected analysis until the restricted evidence is resolved.

Do not infer duplicate status from a shared DICOM key alone, and do not use
targets, predictions, residuals, or performance to choose the retained row.

## 7. Build and compare corrected analyses only when true duplicates exist

First compute the unchanged reviewer metrics from the original predictions so
the later comparison has an exact original counterpart:

```bash
ORIGINAL_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_fixed_original -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 -l mem_per_core=8G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" reviewer-metrics)
```

Then submit the correction stages with scheduler dependencies:

```bash
test ! -e "$JDIM_CORRECTED_ROOT"

AGG_JOB=$(qsub -terse -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_correct_agg -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 -l mem_per_core=16G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" corrected-aggregation)

ANALYSIS_JOB=$(qsub -terse -hold_jid "$AGG_JOB" -cwd -V -P "$JDIM_SGE_PROJECT" \
  -N jdim_correct_models -j y -o "$JDIM_OUTPUT_ROOT/logs" \
  -l h_rt=12:00:00 -pe omp 4 -l mem_per_core=16G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" corrected-analysis)

qsub -cwd -V -P "$JDIM_SGE_PROJECT" -hold_jid "$ANALYSIS_JOB,$ORIGINAL_JOB" \
  -N jdim_correct_compare -j y -o "$JDIM_OUTPUT_ROOT/logs" \
  -l h_rt=04:00:00 -pe omp 2 -l mem_per_core=8G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" corrected-comparison
```

If the corrected root already exists before the first correction job, choose a
new versioned root. On a resumed session after an authorized correction has
started, keep the same root and inspect job/output state rather than resubmitting
an earlier immutable stage.

The aggregation refuses unresolved groups, incomplete one-to-one mapping,
materially divergent vectors, unstable canonical identity, and every existing
output root. The comparison verifies exact study rows, subjects, labels, null
predictions, frozen splits, solver, feature standardization, alpha grid,
bootstrap settings, random seed, hard-extreme rule, and target/cohort counts.
Selected alpha may change because the corrected representation changes; it is
still selected on validation only.

The wrapper reruns primary all-clips and hard-extreme analyses. Before declaring
all dependent results reconciled, inspect the original Phase-2 directory for
view-filtered runs. If a removed source clip was selected by any saved
view-filtered manifest, regenerate that study embedding with the corrected
unique clip store and rerun the corresponding stable-v2 analysis. If this
input-level trace cannot be established, stop with
`BLOCKED_CORRECTED_ANALYSIS_REPRODUCTION`; do not assume the sensitivity is
unaffected.

`corrected-comparison` writes
`aggregate_safe/corrected_analysis_completion_v1.json` only after the
aggregation, all four fixed-protocol refits, corrected reviewer metrics, and
both main and hard-extreme comparisons are present and both comparison
provenance files report verified identity. Absence of that certificate means
the corrected workflow is incomplete, regardless of which directories exist.

After successful correction, repoint the cohort and audit source to the
corrected canonical study store:

```bash
export JDIM_STUDY_EMBEDDING_NPZ="$JDIM_CORRECTED_ROOT/aggregation/restricted/corrected_study_embeddings.npz"
export JDIM_STUDY_EMBEDDING_MANIFEST_CSV="$JDIM_CORRECTED_ROOT/aggregation/restricted/corrected_study_embedding_manifest.csv"
export JDIM_CLIP_EMBEDDING_NPZ="$JDIM_CORRECTED_ROOT/aggregation/restricted/deduplicated_clip_embeddings.npz"
export JDIM_CLIP_EMBEDDING_MANIFEST_CSV="$JDIM_CORRECTED_ROOT/aggregation/restricted/deduplicated_clip_manifest.csv"
"$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" handoff-check
```

## 8. Run repaired cohort flow and original fixed metrics when no correction was needed

If section 7 ran, submit only cohort flow below because the original and
corrected metric jobs are already complete. If no correction was required,
submit both cohort flow and fixed metrics:

```bash
qsub -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_cohort_flow -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 -l mem_per_core=8G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" cohort-flow

qsub -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_fixed_metrics -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 -l mem_per_core=8G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" reviewer-metrics
```

The metrics job reads the unchanged all-split saved prediction CSVs, derives
tertiles from training labels only, and evaluates the held-out test rows. It
does not refit Ridge or the null model. Set `JDIM_NONIMAGE_PREDICTIONS` only if
unchanged row-level comparator predictions with exact study, subject, split,
and target values exist, for example:

```bash
export JDIM_NONIMAGE_PREDICTIONS="demographics=/absolute/restricted/path/to/demographics_predictions.csv"
```

If exact paired comparator rows do not exist, leave the variable unset and
report the paired non-image comparison as unavailable.

## 9. Generate the technical pilot and main audit manifests

Wait for the cohort-flow job to finish successfully. Then create one restricted
opaque-ID key and submit the technical manifest pilot. The pilot records only
reconstruction success and review time; it must not contain content labels or
enter prevalence estimates.

```bash
(umask 077; openssl rand -out "$JDIM_AUDIT_KEY_FILE" 32)
chmod 600 "$JDIM_AUDIT_KEY_FILE"

scripts/scc_run_jdim_tier1.sh validate

qsub -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_audit_pilot -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=01:00:00 -pe omp 1 -l mem_per_core=4G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" audit-pilot
```

Set the DICOM root to the exact approved SCC path confirmed from the canonical
environment; do not infer it from the worktree. Then reconstruct a maximum of
six sampled studies and three canonical clips per study:

```bash
export JDIM_DICOM_DATA_ROOT=/absolute/restricted/confirmed/mimic-iv-echo-dicom-root

qsub -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_audit_reconstruct -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=04:00:00 -pe omp 2 -l mem_per_core=8G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" audit-reconstruct-pilot
```

The reconstruction pilot replays the source preprocessing, verifies the stored
processed array exactly, requires exact equality between replayed and stored
`sampled_indices` plus the stored source-frame count, applies the encoder's
first-32/stride-2 selection, and creates restricted opaque side-by-side contact
sheets. The pilot selects clips by the exact source-row identity in the locked
canonical clip roster and records restricted hashes of each source DICOM,
processed NPZ, sampled-index array, and source/processed model-seen frame array.
It performs no OCR and records no clinical-content labels. A human must open the
restricted HTML, confirm readability and interface usability, and record review
time in the pilot template. If more than 5% of attempted clips fail linkage,
exact replay, or frame-order verification, stop with
`BLOCKED_AUDIT_RECONSTRUCTION`.

After the team confirms reconstruction feasibility and burden without recording
clinical content labels, generate the locked main sample (default 60 studies
per target, proportional across the frozen splits):

```bash
qsub -cwd -V -P "$JDIM_SGE_PROJECT" -N jdim_audit_sample -j y \
  -o "$JDIM_OUTPUT_ROOT/logs" -l h_rt=01:00:00 -pe omp 1 -l mem_per_core=4G \
  -b y "$JDIM_REPO_ROOT/scripts/scc_run_jdim_tier1.sh" audit-sample
```

The sampler does not open or reconstruct DICOMs. Pixel reconstruction and
human review occur later under the approved restricted workflow, using the
opaque reader manifest. It locks every successful canonical clip for every
sampled study into `canonical_clip_roster_restricted.csv` and binds that full
roster to the sample-manifest token. The safe sampling summary reports the
intended clip/read burden without exposing identifiers. Never expose the
linkage CSV, clip roster, source paths, or target values to readers.

## 10. Monitor and account for jobs

```bash
qstat -u "$(whoami)"
tail -f "$JDIM_OUTPUT_ROOT"/logs/jdim_cohort_flow.o*
tail -f "$JDIM_OUTPUT_ROOT"/logs/jdim_fixed_metrics.o*
qacct -j <JOB_ID>
```

Require exit status 0 in `qacct` and a terminal `[done]` line in each log.

## 11. Validate aggregate-safe outputs

```bash
test -s "$JDIM_OUTPUT_ROOT/aggregate_safe/cohort_flow/cohort_flow_invariants.json"
test -s "$JDIM_OUTPUT_ROOT/aggregate_safe/cohort_flow/cohort_flow_summary.json"
test -s "$JDIM_OUTPUT_ROOT/aggregate_safe/reviewer_metrics/continuous_calibration_metrics.csv"
test -s "$JDIM_OUTPUT_ROOT/aggregate_safe/reviewer_metrics/training_tertile_test_error.csv"
test -s "$JDIM_OUTPUT_ROOT/aggregate_safe/reviewer_metrics/paired_delta_mae.csv"

"$JDIM_PYTHON_BIN" -m json.tool \
  "$JDIM_OUTPUT_ROOT/aggregate_safe/cohort_flow/cohort_flow_invariants.json"

if grep -R -n -E '(^|[,"[:space:]])(subject_id|study_id|dicom_id|y_true|y_pred|target_value)([,"[:space:]]|$)|/restricted/|/Users/' \
  "$JDIM_OUTPUT_ROOT/aggregate_safe"; then
  echo "BLOCKED_UNSAFE_AGGREGATE_OUTPUT"
  exit 2
fi
```

The invariant JSON must report `status: ok`. Do not export any aggregate-safe
directory until this scan passes and the team has reviewed every table.

## 12. Aggregate completed manual annotations

After blinded reading and adjudication, keep all annotation inputs restricted:

```bash
"$JDIM_PYTHON_BIN" scripts/prepare_jdim_input_audit.py adjudication-queue \
  --config configs/jdim_input_content_audit_v1.yaml \
  --study-annotations-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/study_annotations.csv" \
  --clip-annotations-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/clip_annotations.csv" \
  --restricted-output-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/adjudication_queue.csv"

# A human adjudicator must copy the immutable queue to a new file, complete
# every queued row, and record a nonempty adjudicator_id. Never overwrite the
# original queue. Aggregation fails closed on missing, extra, stale, or
# incomplete rows.
cp -n \
  "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/adjudication_queue.csv" \
  "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/adjudication_queue_completed.csv"

# HUMAN ACTION REQUIRED: complete adjudicated_value and adjudicator_id in
# adjudication_queue_completed.csv before running either command below.

"$JDIM_PYTHON_BIN" scripts/prepare_jdim_input_audit.py aggregate \
  --config configs/jdim_input_content_audit_v1.yaml \
  --study-annotations-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/study_annotations_final.csv" \
  --clip-annotations-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/clip_annotations_final.csv" \
  --restricted-linkage-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/audit_linkage.csv" \
  --restricted-sampling-design-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/sampling_design_restricted.csv" \
  --restricted-second-reader-manifest-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/second_reader_manifest.csv" \
  --restricted-clip-roster-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/canonical_clip_roster_restricted.csv" \
  --completed-adjudication-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/adjudication_queue_completed.csv" \
  --safe-output-dir "$JDIM_OUTPUT_ROOT/aggregate_safe/input_content_audit/final" \
  --bootstrap-n 2000

# The aggregate command writes manual_audit_completion.json only after the
# exact annotation rosters, independent second reads, sample token, completed
# adjudication, and aggregate-safe outputs all validate.

# Only after all blinded reads and adjudications are locked, allow a separate
# restricted analyst to compare transcribed visible values with report labels.
"$JDIM_PYTHON_BIN" scripts/prepare_jdim_input_audit.py post-unblinding-match \
  --config configs/jdim_input_content_audit_v1.yaml \
  --clip-annotations-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/clip_annotations_final.csv" \
  --restricted-linkage-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/audit_linkage.csv" \
  --restricted-sampling-design-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/sampling_design_restricted.csv" \
  --restricted-clip-roster-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/canonical_clip_roster_restricted.csv" \
  --completed-adjudication-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/adjudication_queue_completed.csv" \
  --manual-audit-completion-json "$JDIM_OUTPUT_ROOT/aggregate_safe/input_content_audit/final/manual_audit_completion.json" \
  --restricted-output-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/post_unblinding_value_matches.csv" \
  --safe-output-dir "$JDIM_OUTPUT_ROOT/aggregate_safe/input_content_audit/post_unblinding_value_matches"
```

The study-level estimand is design-weighted prevalence among sampled studies.
The clip-level output is intentionally labeled
`unweighted_sampled_clip_composition`; it describes the composition of the
locked sampled clips and is not a study- or population-prevalence estimate.
Pre-adjudication reader agreement remains based on the independent locked reads;
completed adjudication supplies the final values used in outcome summaries.

## 13. Build restricted and export-safe provenance manifests

Confirm the EchoPrime release against the existing SCC clone and checkpoint.
The pinned project documentation identifies public EchoPrime release `v1.0.0`
and code commit `03874a5f203695f38968068c21584656d475b6b1`; stop if SCC provenance differs.

```bash
export JDIM_PROVENANCE_SPEC="$JDIM_OUTPUT_ROOT/restricted/lineage/jdim_provenance_spec_v1.json"
export JDIM_RESTRICTED_PROVENANCE="$JDIM_OUTPUT_ROOT/restricted/lineage/jdim_provenance_restricted_v1.json"
export JDIM_SAFE_PROVENANCE="$JDIM_OUTPUT_ROOT/aggregate_safe/jdim_provenance_safe_v1.json"

test ! -e "$JDIM_PROVENANCE_SPEC"
test ! -e "$JDIM_RESTRICTED_PROVENANCE"
test ! -e "$JDIM_SAFE_PROVENANCE"

CANONICAL_EMBEDDING_MANIFEST="$JDIM_FROZEN_STUDY_EMBEDDING_MANIFEST_CSV"
PROVENANCE_MODEL_REFIT=false
CORRECTED_ANALYSIS_COMPLETE=false
CORRECTED_COMPLETION="$JDIM_CORRECTED_ROOT/aggregate_safe/corrected_analysis_completion_v1.json"
CORRECTED_ROLE_ARGS=()
if [[ -f "$CORRECTED_COMPLETION" ]]; then
  CANONICAL_EMBEDDING_MANIFEST="$JDIM_CORRECTED_ROOT/aggregation/restricted/corrected_study_embedding_manifest.csv"
  PROVENANCE_MODEL_REFIT=true
  CORRECTED_ANALYSIS_COMPLETE=true
  CORRECTED_ROLE_ARGS=(
    --file "corrected_study_embedding_manifest=restricted=$JDIM_CORRECTED_ROOT/aggregation/restricted/corrected_study_embedding_manifest.csv"
    --file "corrected_study_embedding_array=restricted=$JDIM_CORRECTED_ROOT/aggregation/restricted/corrected_study_embeddings.npz"
    --file "corrected_clip_embedding_manifest=restricted=$JDIM_CORRECTED_ROOT/aggregation/restricted/deduplicated_clip_manifest.csv"
    --file "corrected_clip_embedding_array=restricted=$JDIM_CORRECTED_ROOT/aggregation/restricted/deduplicated_clip_embeddings.npz"
    --file "corrected_aggregation_provenance=restricted=$JDIM_CORRECTED_ROOT/aggregation/restricted/corrected_aggregation_provenance_restricted.json"
    --file "corrected_main_comparison_provenance=aggregate_safe=$JDIM_CORRECTED_ROOT/aggregate_safe/original_vs_corrected_main/original_vs_corrected_comparison_provenance.json"
    --file "corrected_hard_extremes_comparison_provenance=aggregate_safe=$JDIM_CORRECTED_ROOT/aggregate_safe/original_vs_corrected_hard_extremes/original_vs_corrected_comparison_provenance.json"
    --file "corrected_analysis_completion=aggregate_safe=$CORRECTED_COMPLETION"
  )
elif [[ -e "$JDIM_CORRECTED_ROOT" ]]; then
  echo "[error] Corrected root exists without a completion certificate; provenance is blocked" >&2
  exit 2
fi

PROV_FILE_ARGS=(
  --file "structured_measurements=restricted=$JDIM_STRUCTURED_MEASUREMENTS_CSV"
  --file "selected_study_universe=restricted=$JDIM_SELECTED_STUDIES_CSV"
  --file "source_study_denominator=restricted=$JDIM_SOURCE_STUDIES_CSV"
  --file "embedding_manifest=restricted=$CANONICAL_EMBEDDING_MANIFEST"
  --file "frozen_study_embedding_manifest=restricted=$JDIM_FROZEN_STUDY_EMBEDDING_MANIFEST_CSV"
  --file "frozen_study_embedding_array=restricted=$JDIM_FROZEN_STUDY_EMBEDDING_NPZ"
  --file "historical_clip_embedding_manifest=restricted=$JDIM_HISTORICAL_CLIP_EMBEDDING_MANIFEST_CSV"
  --file "historical_clip_embedding_array=restricted=$JDIM_HISTORICAL_CLIP_EMBEDDING_NPZ"
  --file "split_map=restricted=$JDIM_SPLIT_MAP_CSV"
  --file "video_encoder_checkpoint=restricted=$JDIM_ENCODER_CHECKPOINT"
  --file "audit_configuration=aggregate_safe=$JDIM_AUDIT_CONFIG"
)
PROV_FILE_ARGS+=("${CORRECTED_ROLE_ARGS[@]}")

if [[ -f "$JDIM_OUTPUT_ROOT/restricted/duplicate_forensics/duplicate_forensics_rows.csv" ]]; then
  PROV_FILE_ARGS+=(
    --file "duplicate_forensic_decisions=restricted=$JDIM_OUTPUT_ROOT/restricted/duplicate_forensics/duplicate_forensics_rows.csv"
    --file "duplicate_forensic_provenance=aggregate_safe=$JDIM_OUTPUT_ROOT/aggregate_safe/duplicate_forensics/duplicate_forensics_summary.json"
  )
fi

output_index=0
while IFS= read -r output_path; do
  output_index=$((output_index + 1))
  output_role="aggregate_output_$(printf '%03d' "$output_index")"
  PROV_FILE_ARGS+=(--file "${output_role}=aggregate_safe=${output_path}")
done < <(find "$JDIM_OUTPUT_ROOT/aggregate_safe" -type f ! -name 'jdim_provenance_safe_v1.json' -print | sort)

if [[ "$CORRECTED_ANALYSIS_COMPLETE" == true ]]; then
  corrected_index=0
  while IFS= read -r corrected_path; do
    corrected_index=$((corrected_index + 1))
    corrected_role="corrected_output_$(printf '%03d' "$corrected_index")"
    corrected_classification=aggregate_safe
    if [[ "$corrected_path" == *"/restricted/"* ]]; then
      corrected_classification=restricted
    fi
    PROV_FILE_ARGS+=(--file "${corrected_role}=${corrected_classification}=${corrected_path}")
  done < <(find "$JDIM_CORRECTED_ROOT" -type f -print | sort)
fi

"$JDIM_PYTHON_BIN" scripts/build_jdim_provenance_spec.py \
  --mimic-iv-echo-release "MIMIC-IV-ECHO v1.0" \
  --echoprime-code-release "EchoPrime v1.0.0 commit 03874a5f203695f38968068c21584656d475b6b1" \
  "${PROV_FILE_ARGS[@]}" \
  --argument bootstrap_n=2000 \
  --argument bootstrap_seed=20260824 \
  --argument audit_seed=20260824 \
  --argument model_refit="$PROVENANCE_MODEL_REFIT" \
  --argument corrected_analysis_complete="$CORRECTED_ANALYSIS_COMPLETE" \
  --output-json "$JDIM_PROVENANCE_SPEC"

"$JDIM_PYTHON_BIN" scripts/build_jdim_provenance_manifests.py \
  --spec-json "$JDIM_PROVENANCE_SPEC" \
  --repo-root "$JDIM_REPO_ROOT" \
  --restricted-output-json "$JDIM_RESTRICTED_PROVENANCE" \
  --safe-output-json "$JDIM_SAFE_PROVENANCE"

"$JDIM_PYTHON_BIN" -m json.tool "$JDIM_SAFE_PROVENANCE"
```

The safe manifest must report a clean repository, zero subject overlap, hashes
for every declared file, and no absolute paths or identifiers. When duplicate
correction was required, it must record `model_refit=true` and include every
file under the immutable corrected root, including corrected embeddings,
restricted model outputs, comparisons, and aggregate-safe summaries.
The frozen study parent, historical clip parent, corrected clip child, and
corrected study child must remain separate named roles; the active canonical
manifest must not replace the frozen-parent roles.
