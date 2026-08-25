# JDIM Major Revision Lineage-Repair SCC Runbook

This runbook executes the frozen post-hoc tooling for JDIM-D-26-02840 on SCC.
It does not train or refit a model, regenerate predictions, change the subject
split, optimize thresholds, or inspect DICOM pixels. Row-level source files,
predictions, audit linkage, annotations, and provenance specifications must stay
under approved restricted storage.

Do not proceed if the checked-out commit is dirty, the frozen Phase-2 artifacts
are missing, the split-map lineage cannot be confirmed, or a cohort invariant
returns `BLOCKED_MIXED_OR_UNRESOLVED_LINEAGE`.

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
export JDIM_SOURCE_STUDIES_CSV=/restricted/project/mimicecho/outputs/jdim_major_revision_tier1_v1/restricted/lineage/mimic_iv_echo_source_studies_v1.csv
export JDIM_LINEAGE_JSON="$JDIM_OUTPUT_ROOT/restricted/lineage/jdim_cohort_lineage_v1.json"
export JDIM_SELECTED_STUDIES_CSV="$JDIM_FULLSCALE_ROOT/manifests/all_eligible_studies.csv"
export JDIM_STRUCTURED_MEASUREMENTS_CSV="$JDIM_FULLSCALE_ROOT/manifests/structured_measurements.csv"
export JDIM_SPLIT_MAP_CSV="$JDIM_FULLSCALE_ROOT/manifests/subject_split_map_v1.csv"
export JDIM_SPLIT_MAP_SUMMARY_JSON="$JDIM_FULLSCALE_ROOT/manifests/subject_split_map_v1.summary.json"
export JDIM_STUDY_EMBEDDING_NPZ="$JDIM_FULLSCALE_ROOT/study_embeddings_512/study_embeddings_512.npz"
export JDIM_STUDY_EMBEDDING_MANIFEST_CSV="$JDIM_FULLSCALE_ROOT/study_embeddings_512/study_embedding_manifest.csv"
export JDIM_ENCODER_CHECKPOINT=/restricted/project/mimicecho/echoprime_weights/echo_prime_encoder.pt
export JDIM_LVOT_SUMMARY_JSON="$JDIM_PHASE2_ROOT/lvot_vti/all_clips/imaging_baseline_summary.json"
export JDIM_TAPSE_SUMMARY_JSON="$JDIM_PHASE2_ROOT/tapse/all_clips/imaging_baseline_summary.json"
export JDIM_LVOT_PREDICTIONS_CSV="$JDIM_PHASE2_ROOT/lvot_vti/all_clips/imaging_baseline_predictions.csv"
export JDIM_TAPSE_PREDICTIONS_CSV="$JDIM_PHASE2_ROOT/tapse/all_clips/imaging_baseline_predictions.csv"
export JDIM_AUDIT_CONFIG="$JDIM_REPO_ROOT/configs/jdim_input_content_audit_v1.yaml"
export JDIM_RESTRICTED_AUDIT_ROOT="$JDIM_OUTPUT_ROOT/restricted/input_content_audit"
export JDIM_AUDIT_KEY_FILE="$JDIM_OUTPUT_ROOT/restricted/keys/jdim_audit_hmac_key.bin"
export JDIM_BOOTSTRAP_N=2000
export JDIM_SGE_PROJECT=mimicecho

mkdir -p "$JDIM_OUTPUT_ROOT/restricted/lineage"
mkdir -p "$JDIM_OUTPUT_ROOT/restricted/keys"
mkdir -p "$JDIM_OUTPUT_ROOT/aggregate_safe"
mkdir -p "$JDIM_OUTPUT_ROOT/logs"

test -x "$JDIM_PYTHON_BIN"
"$JDIM_PYTHON_BIN" -c 'import numpy, pandas, scipy, sklearn; print("tier1_python_ok")'
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

This validates headers, the pinned split hash, the audit configuration, and
saved-prediction schemas. It does not compute cohort counts or reviewer
metrics. A nonzero exit or any `BLOCKED_*` status stops execution.

## 6. Submit cohort-flow and fixed-prediction jobs

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

## 7. Generate the technical pilot and main audit manifests

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
opaque reader manifest. Never expose the linkage CSV or target values to
readers.

## 8. Monitor and account for jobs

```bash
qstat -u "$(whoami)"
tail -f "$JDIM_OUTPUT_ROOT"/logs/jdim_cohort_flow.o*
tail -f "$JDIM_OUTPUT_ROOT"/logs/jdim_fixed_metrics.o*
qacct -j <JOB_ID>
```

Require exit status 0 in `qacct` and a terminal `[done]` line in each log.

## 9. Validate aggregate-safe outputs

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

## 10. Aggregate completed manual annotations

After blinded reading and adjudication, keep all annotation inputs restricted:

```bash
"$JDIM_PYTHON_BIN" scripts/prepare_jdim_input_audit.py adjudication-queue \
  --config configs/jdim_input_content_audit_v1.yaml \
  --study-annotations-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/study_annotations.csv" \
  --clip-annotations-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/clip_annotations.csv" \
  --restricted-output-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/adjudication_queue.csv"

"$JDIM_PYTHON_BIN" scripts/prepare_jdim_input_audit.py aggregate \
  --config configs/jdim_input_content_audit_v1.yaml \
  --study-annotations-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/study_annotations_final.csv" \
  --clip-annotations-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/clip_annotations_final.csv" \
  --restricted-linkage-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/audit_linkage.csv" \
  --restricted-sampling-design-csv "$JDIM_OUTPUT_ROOT/restricted/input_content_audit/main/sampling_design_restricted.csv" \
  --safe-output-dir "$JDIM_OUTPUT_ROOT/aggregate_safe/input_content_audit/final" \
  --bootstrap-n 2000
```

## 11. Build restricted and export-safe provenance manifests

Confirm the EchoPrime release against the existing SCC clone and checkpoint.
The pinned project documentation identifies public EchoPrime release `v1.0.0`
and code commit `03874a5f203695f38968068c21584656d475b6b1`; stop if SCC provenance differs.

```bash
export JDIM_PROVENANCE_SPEC="$JDIM_OUTPUT_ROOT/restricted/lineage/jdim_provenance_spec_v1.json"
export JDIM_RESTRICTED_PROVENANCE="$JDIM_OUTPUT_ROOT/restricted/lineage/jdim_provenance_restricted_v1.json"
export JDIM_SAFE_PROVENANCE="$JDIM_OUTPUT_ROOT/aggregate_safe/jdim_provenance_safe_v1.json"

PROV_FILE_ARGS=(
  --file "structured_measurements=restricted=$JDIM_STRUCTURED_MEASUREMENTS_CSV"
  --file "selected_study_universe=restricted=$JDIM_SELECTED_STUDIES_CSV"
  --file "source_study_denominator=restricted=$JDIM_SOURCE_STUDIES_CSV"
  --file "embedding_manifest=restricted=$JDIM_STUDY_EMBEDDING_MANIFEST_CSV"
  --file "split_map=restricted=$JDIM_SPLIT_MAP_CSV"
  --file "video_encoder_checkpoint=restricted=$JDIM_ENCODER_CHECKPOINT"
  --file "audit_configuration=aggregate_safe=$JDIM_AUDIT_CONFIG"
)

output_index=0
while IFS= read -r output_path; do
  output_index=$((output_index + 1))
  output_role="aggregate_output_$(printf '%03d' "$output_index")"
  PROV_FILE_ARGS+=(--file "${output_role}=aggregate_safe=${output_path}")
done < <(find "$JDIM_OUTPUT_ROOT/aggregate_safe" -type f ! -name 'jdim_provenance_safe_v1.json' -print | sort)

"$JDIM_PYTHON_BIN" scripts/build_jdim_provenance_spec.py \
  --mimic-iv-echo-release "MIMIC-IV-ECHO v1.0" \
  --echoprime-code-release "EchoPrime v1.0.0 commit 03874a5f203695f38968068c21584656d475b6b1" \
  "${PROV_FILE_ARGS[@]}" \
  --argument bootstrap_n=2000 \
  --argument bootstrap_seed=20260824 \
  --argument audit_seed=20260824 \
  --argument model_refit=false \
  --output-json "$JDIM_PROVENANCE_SPEC"

"$JDIM_PYTHON_BIN" scripts/build_jdim_provenance_manifests.py \
  --spec-json "$JDIM_PROVENANCE_SPEC" \
  --repo-root "$JDIM_REPO_ROOT" \
  --restricted-output-json "$JDIM_RESTRICTED_PROVENANCE" \
  --safe-output-json "$JDIM_SAFE_PROVENANCE"

"$JDIM_PYTHON_BIN" -m json.tool "$JDIM_SAFE_PROVENANCE"
```

The safe manifest must report a clean repository, zero subject overlap, hashes
for every declared file, and no absolute paths or identifiers.
