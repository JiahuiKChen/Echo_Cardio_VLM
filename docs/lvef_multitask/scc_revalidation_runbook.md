# SCC Phase 1A artifact and denominator audit runbook

This runbook is limited to read-only inspection of historical artifacts and aggregate-only Phase 1A audits. It does not fit a model, regenerate predictions, calculate new test performance, modify the frozen snapshot, download DICOMs, or copy restricted manifests into Git.

## Historical authority and root identity

The root mapping is established by `docs/results_snapshot/2026-04-01_fullscale/snapshot_manifest.json` and its README, not by the inventory filenames:

- Historical generation source: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all` (`ROOT_1` in the safe inventory).
- Historical preservation pack: `/restricted/project/mimicecho/outputs/freeze_fullscale_20260331_111135` (`ROOT_2`), authoritative for included files only after `SHA256SUMS.txt` passes.
- Aggregate-only historical numeric authority in Git: `docs/results_snapshot/2026-04-01_fullscale/`.
- Prior 500-study source merged into the fullscale embedding store: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/stage_d_500study_scc`. Its continued presence must be checked; it was not included in the supplied two-root inventory.

The live fullscale tree is not a whole-tree freeze. Timestamps are secondary evidence only. Match files using the generation scripts, schema, SHA-256 checksums, and the freeze checksum manifest.

## Canonical artifact map

| Requirement | Historical source path | Authority/classification |
|---|---|---|
| One-study-per-subject selected manifest | `ROOT_1/manifests/all_eligible_studies.csv` | Canonical generation source; the filename's “all” does not mean all repeated studies |
| Per-batch requested DICOM records | `ROOT_1/batches/batch_###_records.csv` | Process lineage, not proof of download success |
| DICOM download/read audit | `ROOT_1/batches/batch_###_audit/dicom_audit.csv` | Canonical per-batch lineage; `exists` and `read_ok` define different stages. Funnel `n_studies` means at least one successful row, while the audit separately reports studies with all rows successful |
| Cine candidates | `ROOT_1/batches/batch_###_audit/cine_candidates.csv` | Canonical per-batch multiframe candidate lineage |
| Cine extraction | `ROOT_1/batches/batch_###_extraction_manifest.csv` | Canonical per-batch extraction lineage; use `write_ok` |
| Per-batch clip embeddings | `ROOT_1/batches/batch_###_embeddings/clip_embeddings_512.npz`, `clip_embedding_manifest.csv`, and `clip_embedding_manifest.summary.json` | Process lineage for the nine new batches |
| Merged clip embeddings | `ROOT_1/merged_clip_embeddings_512/clip_embeddings_512.npz`, `clip_embedding_manifest.csv`, and `clip_embedding_manifest.summary.json` | Canonical historical clip store; includes the prior 500-study source |
| Study embeddings | `ROOT_1/study_embeddings_512/study_embeddings_512.npz`, `study_embedding_manifest.csv`, and `study_embedding_manifest.summary.json` | Canonical historical mean-pooled study store |
| Structured measurements | `ROOT_1/manifests/structured_measurements.csv` | Canonical restricted structured export |
| Subject split map | `ROOT_1/manifests/subject_split_map_v1.csv` | Canonical historical split authority |
| LVEF label manifest | `ROOT_1/manifests/lvef_still_manifest.csv` | Canonical historical linked LVEF cohort |
| Raw-to-canonical map | `ROOT_1/measurement_registry_v1/measurement_to_canonical_mapping.csv` | Canonical historical mapping |
| Measurement registry | `ROOT_1/measurement_registry_v1/measurement_registry_raw.csv`, `measurement_registry_canonical.csv`, `selected_measurement_tasks.csv`, and `excluded_measurement_tasks.csv` | Canonical seven-file registry lineage; the last two names follow directly from the generator and require presence/schema verification |
| Strict 29-task definition | `ROOT_1/task_panel_v1_strict/multitask_tasks_kept.csv`, `multitask_task_metadata.csv`, and `multitask_task_panel.summary.json` | Headline panel definition/provenance |
| Strict 29-task linked data | `ROOT_1/task_panel_v1_strict/multitask_panel_wide.csv` and `multitask_panel_long.csv` | Restricted task-label panels |
| Historical tabular feature list | Top-level `retained_features` in `ROOT_1/eval_e3_tabular/measurement_leakage_audit.json`; `feature_cols` is also recorded in the fusion metrics JSON | No standalone feature-list file was generated |
| LVEF vision artifacts | `ROOT_1/eval_e2b_vision/echoprime_embedding_baseline_metrics.json`, `echoprime_embedding_clip_predictions.csv`, and `echoprime_embedding_study_predictions.csv` | Canonical historical outputs; retain legacy directory name only for lineage |
| LVEF structured artifacts | `ROOT_1/eval_e3_tabular/tabular_baseline_metrics.json`, `measurement_leakage_audit.json`, and `tabular_predictions.csv` | Canonical historical outputs |
| LVEF fusion artifacts | `ROOT_1/eval_e5_fusion/fusion_metrics.json` and `fusion_comparison_table.csv` | Canonical historical point-estimate outputs; the runner did not write fusion predictions |
| LVEF bootstrap artifacts | `ROOT_1/eval_e5_fusion_boot10k/fusion_metrics.json` and `fusion_comparison_table.csv` | Higher-resample model-specific AUROC interval rerun; no raw draws or predictions |
| Strict multitask predictions | `ROOT_1/eval_multitask_{vision,tabular,fusion}_strict/*_predictions_long.csv` | Canonical historical row-level predictions for Phase 1A denominator checks |
| Strict multitask metrics | The corresponding `*_task_metrics.csv` and `*.summary.json` in those three directories | Canonical historical outputs |
| EchoPrime encoder checkpoint | `/restricted/project/mimicecho/echoprime_weights/echo_prime_encoder.pt` | Exact filename used by the encoder-only extraction script; current SHA-256 must be recorded, but it proves historical identity only if freeze metadata links it |
| Environment/package metadata | `ROOT_2/meta/*` | Exact filenames unresolved by the filtered inventory; inspect filenames, byte counts, checksums, and structure before assigning authority |

The encoder-only pipeline did not use `view_classifier.pt`.

## Frozen, duplicate, exploratory, and obsolete artifacts

- `ROOT_2/eval/*` and `ROOT_2/manifests/*` are frozen copies/subsets, not independent analysis runs. Treat a proposed pair as identical only after the freeze checksum passes and the two files compare byte-for-byte or by SHA-256.
- `ROOT_2` contains no observed multitask directory. The Git aggregate snapshot preserves the multitask headline results, while the restricted row-level multitask outputs remain in `ROOT_1` unless a separate checksum bundle is found.
- `task_panel_v1_all` is the broader 38-task exploratory panel. It is not the strict 29-task headline panel.
- `eval_multitask_vision_all` is an exploratory broad-panel vision run. It is not interchangeable with `eval_multitask_vision_strict`.
- `eval_e5_fusion_boot10k` is a deterministic rerun of the same model specification/training procedure with more bootstrap resamples, pending metric/config identity checks; the runner refits models on each invocation. It is not automatically interchangeable with the original point-estimate artifact.
- `reporting_assets` and `reporting_multitask_assets` are presentation derivatives. Their source metrics/predictions remain upstream authorities.
- E2b/E3/E5 names are immutable legacy directory names. New analyses and prose use vision-only, structured-only, and early fusion with explicit panel/model versions.

## Missing or regeneration-required artifacts

The inventory does not establish the following:

1. A complete 7,243-study public-DICOM manifest or a stored 7,104-study eligible-before-one-study-per-subject manifest. The historical SQL materialized only the selected row (`rn_subject <= 1`). Either reconstruction would be a new read-only reconstruction, not a historical artifact.
2. One consolidated download/read/extraction manifest. Phase 1A concatenates retained per-batch manifests in memory; it does not write patient-level combined manifests into Git.
3. Complete Stage-D lineage inside either inventoried root. The exact script-derived candidate paths are checked below before use.
4. LVEF fusion predictions. Their absence is by implementation, so three-way LVEF common-denominator verification and paired LVEF bootstrap differences are blocked until a separately authorized deterministic model-only regeneration.
5. Raw bootstrap replicate arrays. Existing JSON contains only model-specific summaries and cannot support paired delta inference.
6. A standalone tabular feature-list file. It can later be deterministically extracted from the historical leakage-audit JSON after provenance lock.
7. Definitive historical package/checkpoint identity unless the two freeze metadata files record it. Hashing the current checkpoint cannot retroactively prove that it was used.
8. A whole-tree historical checksum for `ROOT_1`. A checksum created now would preserve current state only.

## Exact SCC command block

Run this from the dedicated SCC worktree after pulling the portability commit. Expected nonzero audit status `1` means a scientific discrepancy was detected and is recorded; status `2` or greater is an input/tooling blocker and stops the block.

### 1. Verify branch and lightweight dependencies

```bash
set -euo pipefail

LVEF_WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
LVEF_BRANCH=codex/lvef-multitask-revalidation

git -C "$LVEF_WORKTREE" fetch origin --prune
git -C "$LVEF_WORKTREE" switch "$LVEF_BRANCH"
git -C "$LVEF_WORKTREE" pull --ff-only origin "$LVEF_BRANCH"

cd "$LVEF_WORKTREE"
test "$(git branch --show-current)" = "$LVEF_BRANCH"
test -z "$(git status --porcelain)"
git merge-base --is-ancestor 23c74cc HEAD

PYTHON_RESOLUTION_ROOT="$(
  mktemp -d /restricted/project/mimicecho/audits/lvef_python_resolution_XXXXXX
)"
LVEF_SCC_PYTHON_RESOLVED="$(
  scripts/resolve_lvef_scc_python.sh \
    --record-json "$PYTHON_RESOLUTION_ROOT/lvef_scc_python_environment.json"
)"
readonly LVEF_SCC_PYTHON_RESOLVED

cat "$PYTHON_RESOLUTION_ROOT/lvef_scc_python_environment.json"
"$LVEF_SCC_PYTHON_RESOLVED" scripts/run_phase1a_tests.py
```

### 2. Define portable file listing, real roots, and a unique audit directory

The helper prefers Ripgrep but works on the current SCC host with `find`.

```bash
portable_list_files() {
  local root="$1"
  if command -v rg >/dev/null 2>&1; then
    (cd "$root" && rg --files --hidden --no-ignore .)
  else
    (cd "$root" && find . -type f -print)
  fi | sed 's#^\./##' | LC_ALL=C sort
}

FULLSCALE_ROOT=/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all
FREEZE_ROOT=/restricted/project/mimicecho/outputs/freeze_fullscale_20260331_111135
STAGE_D_ROOT=/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/stage_d_500study_scc
ECHOPRIME_ENCODER=/restricted/project/mimicecho/echoprime_weights/echo_prime_encoder.pt

CURRENT_COMMIT="$(git rev-parse HEAD)"
RUN_CONFIG_SHA="$(
  printf '%s\n' "$CURRENT_COMMIT" "$FULLSCALE_ROOT" "$FREEZE_ROOT" "$STAGE_D_ROOT" | sha256sum | awk '{print $1}'
)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_${CURRENT_COMMIT:0:12}_${RUN_CONFIG_SHA:0:12}"
PHASE1_AUDIT_ROOT="/restricted/project/mimicecho/audits/lvef_multitask_phase1a_${RUN_ID}"
PHASE1_AGGREGATE_DIR="$PHASE1_AUDIT_ROOT/aggregate"
PHASE1_RESTRICTED_DIR="$PHASE1_AUDIT_ROOT/restricted"

test ! -e "$PHASE1_AUDIT_ROOT"
mkdir -p "$PHASE1_AGGREGATE_DIR" "$PHASE1_RESTRICTED_DIR"

run_audit() {
  local label="$1"
  shift
  local status
  set +e
  "$@"
  status=$?
  LAST_AUDIT_STATUS=$status
  set -e
  printf '%s\t%s\n' "$label" "$status" | tee -a "$PHASE1_AGGREGATE_DIR/audit_exit_status.tsv"
  if [[ "$status" -ge 2 ]]; then
    return "$status"
  fi
  return 0
}

portable_list_files "$FULLSCALE_ROOT" > "$PHASE1_RESTRICTED_DIR/fullscale_relative_file_inventory.txt"
portable_list_files "$FREEZE_ROOT" > "$PHASE1_AGGREGATE_DIR/freeze_relative_file_inventory.txt"
```

The fullscale file inventory remains restricted because it may expose operational path structure. The freeze inventory is safe to paste because it contains freeze-relative filenames only.

### 3. Resolve exact historical files and require the nine fullscale batches

```bash
SELECTED_STUDIES="$FULLSCALE_ROOT/manifests/all_eligible_studies.csv"
STRUCTURED_MEASUREMENTS="$FULLSCALE_ROOT/manifests/structured_measurements.csv"
LVEF_LABELS="$FULLSCALE_ROOT/manifests/lvef_still_manifest.csv"
SPLIT_MAP="$FULLSCALE_ROOT/manifests/subject_split_map_v1.csv"

MERGED_CLIP_NPZ="$FULLSCALE_ROOT/merged_clip_embeddings_512/clip_embeddings_512.npz"
MERGED_CLIP_MANIFEST="$FULLSCALE_ROOT/merged_clip_embeddings_512/clip_embedding_manifest.csv"
MERGED_CLIP_SUMMARY="$FULLSCALE_ROOT/merged_clip_embeddings_512/clip_embedding_manifest.summary.json"
STUDY_EMBEDDING_NPZ="$FULLSCALE_ROOT/study_embeddings_512/study_embeddings_512.npz"
STUDY_EMBEDDING_MANIFEST="$FULLSCALE_ROOT/study_embeddings_512/study_embedding_manifest.csv"
STUDY_EMBEDDING_SUMMARY="$FULLSCALE_ROOT/study_embeddings_512/study_embedding_manifest.summary.json"

REGISTRY_RAW="$FULLSCALE_ROOT/measurement_registry_v1/measurement_registry_raw.csv"
REGISTRY_CANONICAL="$FULLSCALE_ROOT/measurement_registry_v1/measurement_registry_canonical.csv"
RAW_CANONICAL_MAPPING="$FULLSCALE_ROOT/measurement_registry_v1/measurement_to_canonical_mapping.csv"
REGISTRY_SELECTED="$FULLSCALE_ROOT/measurement_registry_v1/selected_measurement_tasks.csv"
REGISTRY_EXCLUDED="$FULLSCALE_ROOT/measurement_registry_v1/excluded_measurement_tasks.csv"

STRICT_PANEL="$FULLSCALE_ROOT/task_panel_v1_strict/multitask_panel_wide.csv"
STRICT_PANEL_LONG="$FULLSCALE_ROOT/task_panel_v1_strict/multitask_panel_long.csv"
STRICT_TASKS="$FULLSCALE_ROOT/task_panel_v1_strict/multitask_tasks_kept.csv"
STRICT_TASK_METADATA="$FULLSCALE_ROOT/task_panel_v1_strict/multitask_task_metadata.csv"
STRICT_PANEL_SUMMARY="$FULLSCALE_ROOT/task_panel_v1_strict/multitask_task_panel.summary.json"

LVEF_VISION_METRICS="$FULLSCALE_ROOT/eval_e2b_vision/echoprime_embedding_baseline_metrics.json"
LVEF_VISION_CLIP_PREDICTIONS="$FULLSCALE_ROOT/eval_e2b_vision/echoprime_embedding_clip_predictions.csv"
LVEF_VISION_STUDY_PREDICTIONS="$FULLSCALE_ROOT/eval_e2b_vision/echoprime_embedding_study_predictions.csv"
LVEF_STRUCTURED_METRICS="$FULLSCALE_ROOT/eval_e3_tabular/tabular_baseline_metrics.json"
LVEF_STRUCTURED_LEAKAGE="$FULLSCALE_ROOT/eval_e3_tabular/measurement_leakage_audit.json"
LVEF_STRUCTURED_PREDICTIONS="$FULLSCALE_ROOT/eval_e3_tabular/tabular_predictions.csv"
LVEF_FUSION_METRICS="$FULLSCALE_ROOT/eval_e5_fusion/fusion_metrics.json"
LVEF_FUSION_TABLE="$FULLSCALE_ROOT/eval_e5_fusion/fusion_comparison_table.csv"
LVEF_FUSION_BOOT_METRICS="$FULLSCALE_ROOT/eval_e5_fusion_boot10k/fusion_metrics.json"
LVEF_FUSION_BOOT_TABLE="$FULLSCALE_ROOT/eval_e5_fusion_boot10k/fusion_comparison_table.csv"

MT_VISION_PREDICTIONS="$FULLSCALE_ROOT/eval_multitask_vision_strict/multitask_vision_predictions_long.csv"
MT_STRUCTURED_PREDICTIONS="$FULLSCALE_ROOT/eval_multitask_tabular_strict/multitask_tabular_predictions_long.csv"
MT_FUSION_PREDICTIONS="$FULLSCALE_ROOT/eval_multitask_fusion_strict/multitask_fusion_predictions_long.csv"
MT_VISION_METRICS="$FULLSCALE_ROOT/eval_multitask_vision_strict/multitask_vision_task_metrics.csv"
MT_STRUCTURED_METRICS="$FULLSCALE_ROOT/eval_multitask_tabular_strict/multitask_tabular_task_metrics.csv"
MT_FUSION_METRICS="$FULLSCALE_ROOT/eval_multitask_fusion_strict/multitask_fusion_task_metrics.csv"

STAGE_D_SELECTED="$STAGE_D_ROOT/manifests/selected_studies.csv"
STAGE_D_DICOM_AUDIT="$STAGE_D_ROOT/audit/dicom_audit.csv"
STAGE_D_CINE_CANDIDATES="$STAGE_D_ROOT/audit/cine_candidates.csv"
STAGE_D_EXTRACTION="$STAGE_D_ROOT/extract_allclip/extraction_manifest.csv"
STAGE_D_CLIP_NPZ="$STAGE_D_ROOT/echoprime_embeddings_512/clip_embeddings_512.npz"
STAGE_D_CLIP_MANIFEST="$STAGE_D_ROOT/echoprime_embeddings_512/clip_embedding_manifest.csv"
STAGE_D_CLIP_SUMMARY="$STAGE_D_ROOT/echoprime_embeddings_512/clip_embedding_manifest.summary.json"
BATCH_MANIFEST="$FULLSCALE_ROOT/batches/batch_manifest.json"

shopt -s nullglob
BATCH_STUDY_MANIFESTS=("$FULLSCALE_ROOT"/batches/batch_*_studies.csv)
BATCH_RECORD_MANIFESTS=("$FULLSCALE_ROOT"/batches/batch_*_records.csv)
BATCH_DICOM_AUDITS=("$FULLSCALE_ROOT"/batches/batch_*_audit/dicom_audit.csv)
BATCH_CINE_CANDIDATES=("$FULLSCALE_ROOT"/batches/batch_*_audit/cine_candidates.csv)
BATCH_EXTRACTION_MANIFESTS=("$FULLSCALE_ROOT"/batches/batch_*_extraction_manifest.csv)
BATCH_CLIP_NPZS=("$FULLSCALE_ROOT"/batches/batch_*_embeddings/clip_embeddings_512.npz)
BATCH_CLIP_MANIFESTS=("$FULLSCALE_ROOT"/batches/batch_*_embeddings/clip_embedding_manifest.csv)
BATCH_CLIP_SUMMARIES=("$FULLSCALE_ROOT"/batches/batch_*_embeddings/clip_embedding_manifest.summary.json)
FREEZE_META_FILES=("$FREEZE_ROOT"/meta/*)

test "${#BATCH_STUDY_MANIFESTS[@]}" -eq 9
test "${#BATCH_RECORD_MANIFESTS[@]}" -eq 9
test "${#BATCH_DICOM_AUDITS[@]}" -eq 9
test "${#BATCH_CINE_CANDIDATES[@]}" -eq 9
test "${#BATCH_EXTRACTION_MANIFESTS[@]}" -eq 9
test "${#BATCH_CLIP_NPZS[@]}" -eq 9
test "${#BATCH_CLIP_MANIFESTS[@]}" -eq 9
test "${#BATCH_CLIP_SUMMARIES[@]}" -eq 9
test "${#FREEZE_META_FILES[@]}" -eq 2

for index in {0..8}; do
  printf -v batch_tag 'batch_%03d' "$index"
  test -f "$FULLSCALE_ROOT/batches/${batch_tag}_studies.csv"
  test -f "$FULLSCALE_ROOT/batches/${batch_tag}_records.csv"
  test -f "$FULLSCALE_ROOT/batches/${batch_tag}_audit/dicom_audit.csv"
  test -f "$FULLSCALE_ROOT/batches/${batch_tag}_audit/cine_candidates.csv"
  test -f "$FULLSCALE_ROOT/batches/${batch_tag}_extraction_manifest.csv"
  test -f "$FULLSCALE_ROOT/batches/${batch_tag}_embeddings/clip_embeddings_512.npz"
  test -f "$FULLSCALE_ROOT/batches/${batch_tag}_embeddings/clip_embedding_manifest.csv"
  test -f "$FULLSCALE_ROOT/batches/${batch_tag}_embeddings/clip_embedding_manifest.summary.json"
done

STAGE_D_COMPLETE=true
for required in \
  "$STAGE_D_SELECTED" \
  "$STAGE_D_DICOM_AUDIT" \
  "$STAGE_D_CINE_CANDIDATES" \
  "$STAGE_D_EXTRACTION" \
  "$STAGE_D_CLIP_NPZ" \
  "$STAGE_D_CLIP_MANIFEST" \
  "$STAGE_D_CLIP_SUMMARY"; do
  if [[ ! -f "$required" ]]; then
    STAGE_D_COMPLETE=false
  fi
done
printf 'stage_d_lineage_complete\t%s\n' "$STAGE_D_COMPLETE" | tee "$PHASE1_AGGREGATE_DIR/stage_d_lineage_status.tsv"

BATCH_STUDY_ARGS=()
for item in "${BATCH_STUDY_MANIFESTS[@]}"; do
  BATCH_STUDY_ARGS+=(--batch-studies "$item")
done
PRIOR_STAGE_PARTITION_ARGS=()
if [[ -f "$STAGE_D_SELECTED" ]]; then
  PRIOR_STAGE_PARTITION_ARGS+=(--prior-stage-studies "$STAGE_D_SELECTED")
fi
run_audit batch_study_partition \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/audit_batch_study_partition.py \
  --selected-studies "$SELECTED_STUDIES" \
  "${PRIOR_STAGE_PARTITION_ARGS[@]}" \
  --batch-manifest "$BATCH_MANIFEST" \
  "${BATCH_STUDY_ARGS[@]}" \
  --expected-batches 9 \
  --output-json "$PHASE1_AGGREGATE_DIR/batch_study_partition.json" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/batch_study_partition"
BATCH_PARTITION_STATUS=$LAST_AUDIT_STATUS
```

If `stage_d_lineage_complete` is false or `batch_study_partition` has nonzero status, continue only with the explicitly incomplete overlap classification below. The prior selected-study manifest is still used for source attribution whenever present, but do not claim a locked download/read/extraction funnel until both lineage gates resolve.

### 4. Validate the preservation pack and proposed duplicate pairs

```bash
set +e
(
  cd "$FREEZE_ROOT"
  sha256sum -c SHA256SUMS.txt
) > "$PHASE1_RESTRICTED_DIR/freeze_checksum_validation_raw.txt" 2>&1
FREEZE_CHECKSUM_STATUS=$?
set -e
if [[ "$FREEZE_CHECKSUM_STATUS" -eq 0 ]]; then
  FREEZE_CHECKSUM_RESULT=PASS
else
  FREEZE_CHECKSUM_RESULT=FAIL
fi
printf 'check\tstatus\nfreeze_sha256_manifest\t%s\n' "$FREEZE_CHECKSUM_RESULT" \
  | tee "$PHASE1_AGGREGATE_DIR/freeze_checksum_validation.tsv"

compare_pair() {
  local alias="$1"
  local left="$2"
  local right="$3"
  local status
  if [[ ! -f "$left" || ! -f "$right" ]]; then
    status=MISSING_MEMBER
  elif cmp -s "$left" "$right"; then
    status=IDENTICAL
  else
    status=DIFFERENT
  fi
  printf '%s\t%s\n' "$alias" "$status"
}

{
  printf 'pair\tstatus\n'
  compare_pair lvef_vision_metrics \
    "$LVEF_VISION_METRICS" \
    "$FREEZE_ROOT/eval/echoprime_embedding_baseline_metrics.json"
  compare_pair lvef_structured_metrics \
    "$LVEF_STRUCTURED_METRICS" \
    "$FREEZE_ROOT/eval/tabular_baseline_metrics.json"
  compare_pair lvef_structured_leakage \
    "$LVEF_STRUCTURED_LEAKAGE" \
    "$FREEZE_ROOT/eval/measurement_leakage_audit.json"
  compare_pair lvef_fusion_metrics \
    "$LVEF_FUSION_METRICS" \
    "$FREEZE_ROOT/eval/fusion_metrics.json"
  compare_pair lvef_fusion_table \
    "$LVEF_FUSION_TABLE" \
    "$FREEZE_ROOT/eval/fusion_comparison_table.csv"
  for frozen in "$FREEZE_ROOT"/manifests/*; do
    name="${frozen##*/}"
    compare_pair "manifest_${name}" \
      "$FULLSCALE_ROOT/manifests/$name" \
      "$frozen"
  done
} | tee "$PHASE1_AGGREGATE_DIR/freeze_duplicate_pair_status.tsv"
```

A failed freeze checksum or a `MISSING_MEMBER`/`DIFFERENT` pair is a blocker to calling that file a validated frozen duplicate; it does not authorize replacing either file. Raw checksum-verifier output stays restricted because checksum-manifest entry names have not yet passed the aggregate-safety gate.

### 5. Run checksum/schema-only inspection

This command emits aliases, byte counts, SHA-256 hashes, CSV column names and row counts, JSON container sizes/type histograms without source key names, and NPZ shapes/dtypes. It emits no supplied path and no row, label, prediction, identifier, embedding value, JSON scalar value, or source-derived JSON key.

```bash
SCHEMA_ARGS=(
  --artifact "selected_studies=$SELECTED_STUDIES"
  --artifact "merged_clip_npz=$MERGED_CLIP_NPZ"
  --artifact "merged_clip_manifest=$MERGED_CLIP_MANIFEST"
  --artifact "merged_clip_summary=$MERGED_CLIP_SUMMARY"
  --artifact "study_embedding_npz=$STUDY_EMBEDDING_NPZ"
  --artifact "study_embedding_manifest=$STUDY_EMBEDDING_MANIFEST"
  --artifact "study_embedding_summary=$STUDY_EMBEDDING_SUMMARY"
  --artifact "structured_measurements=$STRUCTURED_MEASUREMENTS"
  --artifact "lvef_labels=$LVEF_LABELS"
  --artifact "subject_split_map=$SPLIT_MAP"
  --artifact "registry_raw=$REGISTRY_RAW"
  --artifact "registry_canonical=$REGISTRY_CANONICAL"
  --artifact "raw_canonical_mapping=$RAW_CANONICAL_MAPPING"
  --artifact "registry_selected=$REGISTRY_SELECTED"
  --artifact "registry_excluded=$REGISTRY_EXCLUDED"
  --artifact "strict_panel_wide=$STRICT_PANEL"
  --artifact "strict_panel_long=$STRICT_PANEL_LONG"
  --artifact "strict_tasks=$STRICT_TASKS"
  --artifact "strict_task_metadata=$STRICT_TASK_METADATA"
  --artifact "strict_panel_summary=$STRICT_PANEL_SUMMARY"
  --artifact "batch_manifest=$BATCH_MANIFEST"
  --artifact "lvef_vision_metrics=$LVEF_VISION_METRICS"
  --artifact "lvef_vision_clip_predictions=$LVEF_VISION_CLIP_PREDICTIONS"
  --artifact "lvef_vision_study_predictions=$LVEF_VISION_STUDY_PREDICTIONS"
  --artifact "lvef_structured_metrics=$LVEF_STRUCTURED_METRICS"
  --artifact "lvef_structured_leakage=$LVEF_STRUCTURED_LEAKAGE"
  --artifact "lvef_structured_predictions=$LVEF_STRUCTURED_PREDICTIONS"
  --artifact "lvef_fusion_metrics=$LVEF_FUSION_METRICS"
  --artifact "lvef_fusion_table=$LVEF_FUSION_TABLE"
  --artifact "lvef_fusion_boot_metrics=$LVEF_FUSION_BOOT_METRICS"
  --artifact "lvef_fusion_boot_table=$LVEF_FUSION_BOOT_TABLE"
  --artifact "multitask_vision_predictions=$MT_VISION_PREDICTIONS"
  --artifact "multitask_structured_predictions=$MT_STRUCTURED_PREDICTIONS"
  --artifact "multitask_fusion_predictions=$MT_FUSION_PREDICTIONS"
  --artifact "multitask_vision_metrics=$MT_VISION_METRICS"
  --artifact "multitask_structured_metrics=$MT_STRUCTURED_METRICS"
  --artifact "multitask_fusion_metrics=$MT_FUSION_METRICS"
  --artifact "echoprime_encoder=$ECHOPRIME_ENCODER"
)
EMBEDDING_PAIR_ARGS=(
  --embedding-pair "merged_clip=$MERGED_CLIP_NPZ,$MERGED_CLIP_MANIFEST"
  --embedding-pair "study_store=$STUDY_EMBEDDING_NPZ,$STUDY_EMBEDDING_MANIFEST"
)

for index in "${!BATCH_DICOM_AUDITS[@]}"; do
  SCHEMA_ARGS+=(--artifact "batch_studies_${index}=${BATCH_STUDY_MANIFESTS[$index]}")
  SCHEMA_ARGS+=(--artifact "batch_records_${index}=${BATCH_RECORD_MANIFESTS[$index]}")
  SCHEMA_ARGS+=(--artifact "batch_dicom_audit_${index}=${BATCH_DICOM_AUDITS[$index]}")
  SCHEMA_ARGS+=(--artifact "batch_cine_candidates_${index}=${BATCH_CINE_CANDIDATES[$index]}")
  SCHEMA_ARGS+=(--artifact "batch_extraction_${index}=${BATCH_EXTRACTION_MANIFESTS[$index]}")
  SCHEMA_ARGS+=(--artifact "batch_clip_npz_${index}=${BATCH_CLIP_NPZS[$index]}")
  SCHEMA_ARGS+=(--artifact "batch_clip_manifest_${index}=${BATCH_CLIP_MANIFESTS[$index]}")
  SCHEMA_ARGS+=(--artifact "batch_clip_summary_${index}=${BATCH_CLIP_SUMMARIES[$index]}")
  EMBEDDING_PAIR_ARGS+=(
    --embedding-pair "batch_clip_${index}=${BATCH_CLIP_NPZS[$index]},${BATCH_CLIP_MANIFESTS[$index]}"
  )
done

for index in "${!FREEZE_META_FILES[@]}"; do
  SCHEMA_ARGS+=(--artifact "freeze_meta_${index}=${FREEZE_META_FILES[$index]}")
done

if [[ "$STAGE_D_COMPLETE" == true ]]; then
  SCHEMA_ARGS+=(
    --artifact "stage_d_selected=$STAGE_D_SELECTED"
    --artifact "stage_d_dicom_audit=$STAGE_D_DICOM_AUDIT"
    --artifact "stage_d_cine_candidates=$STAGE_D_CINE_CANDIDATES"
    --artifact "stage_d_extraction=$STAGE_D_EXTRACTION"
    --artifact "stage_d_clip_npz=$STAGE_D_CLIP_NPZ"
    --artifact "stage_d_clip_manifest=$STAGE_D_CLIP_MANIFEST"
    --artifact "stage_d_clip_summary=$STAGE_D_CLIP_SUMMARY"
  )
  EMBEDDING_PAIR_ARGS+=(
    --embedding-pair "stage_d_clip=$STAGE_D_CLIP_NPZ,$STAGE_D_CLIP_MANIFEST"
  )
fi

"$LVEF_SCC_PYTHON_RESOLVED" scripts/inspect_artifact_schemas.py \
  "${SCHEMA_ARGS[@]}" \
  "${EMBEDDING_PAIR_ARGS[@]}" \
  --embedding-width 512 \
  --output-json "$PHASE1_AGGREGATE_DIR/artifact_schema_checksums.json"
```

This schema gate must finish with both `n_blocking: 0` and `n_embedding_pair_blocking: 0` before the paths are treated as resolved. It checks the nine current batch pairs, the Stage-D pair when Stage D is complete, the merged clip pair, and the study pair, plus each generator summary JSON. Each pair requires a nonempty float32 `embeddings` array of width 512; valid success flags and at least one successful row; exactly one subject, study, and embedding-index column; nonblank identifiers; one-subject-per-study consistency; finite, integer, nonnegative, unique, complete index coverage; study-ID uniqueness for study-indexed stores; and exact successful-manifest-row versus array-row alignment. The two `freeze_meta_*` aliases map in lexical order to the two `meta/*` names in `freeze_relative_file_inventory.txt`.

### 6. Artifact, overlap, and denominator/split audits

```bash
ARTIFACT_STAGE_ARGS=()
OVERLAP_STAGE_ARGS=()
CLIP_COMPONENT_ARGS=()
for item in "${BATCH_DICOM_AUDITS[@]}"; do
  ARTIFACT_STAGE_ARGS+=(--downloaded-studies "$item" --readable-dicoms "$item")
  OVERLAP_STAGE_ARGS+=(--downloaded-studies "$item" --readable-dicoms "$item")
done
for item in "${BATCH_CINE_CANDIDATES[@]}"; do
  ARTIFACT_STAGE_ARGS+=(--cine-candidates "$item")
  OVERLAP_STAGE_ARGS+=(--cine-candidates "$item")
done
for item in "${BATCH_EXTRACTION_MANIFESTS[@]}"; do
  ARTIFACT_STAGE_ARGS+=(--extracted-clips "$item")
  OVERLAP_STAGE_ARGS+=(--extracted-clips "$item")
done

OVERLAP_COMPLETENESS_ARGS=()
if [[ -f "$STAGE_D_SELECTED" ]]; then
  OVERLAP_STAGE_ARGS+=(--prior-stage-studies "$STAGE_D_SELECTED")
fi
if [[ "$STAGE_D_COMPLETE" == true ]]; then
  ARTIFACT_STAGE_ARGS+=(
    --downloaded-studies "$STAGE_D_DICOM_AUDIT"
    --readable-dicoms "$STAGE_D_DICOM_AUDIT"
    --cine-candidates "$STAGE_D_CINE_CANDIDATES"
    --extracted-clips "$STAGE_D_EXTRACTION"
  )
  OVERLAP_STAGE_ARGS+=(
    --downloaded-studies "$STAGE_D_DICOM_AUDIT"
    --readable-dicoms "$STAGE_D_DICOM_AUDIT"
    --cine-candidates "$STAGE_D_CINE_CANDIDATES"
    --extracted-clips "$STAGE_D_EXTRACTION"
  )
  CLIP_COMPONENT_ARGS+=(
    --clip-component-lineage-complete
    --expected-clip-components 10
    --clip-embedding-component-manifest "$STAGE_D_CLIP_MANIFEST"
  )
  for item in "${BATCH_CLIP_MANIFESTS[@]}"; do
    CLIP_COMPONENT_ARGS+=(--clip-embedding-component-manifest "$item")
  done
  if [[ "$BATCH_PARTITION_STATUS" -eq 0 ]]; then
    OVERLAP_COMPLETENESS_ARGS+=(--stage-lineage-complete)
  fi
fi

run_audit artifact_audit \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/audit_lvef_multitask_artifacts.py \
  --selected-studies "$SELECTED_STUDIES" \
  "${ARTIFACT_STAGE_ARGS[@]}" \
  --clip-embeddings "$MERGED_CLIP_MANIFEST" \
  "${CLIP_COMPONENT_ARGS[@]}" \
  --study-embeddings "$STUDY_EMBEDDING_MANIFEST" \
  --structured-measurements "$STRUCTURED_MEASUREMENTS" \
  --lvef-labels "$LVEF_LABELS" \
  --split-map "$SPLIT_MAP" \
  --multitask-panel "$STRICT_PANEL" \
  --require-artifact selected_studies \
  --require-artifact downloaded_studies \
  --require-artifact readable_dicoms \
  --require-artifact cine_candidates \
  --require-artifact extracted_clips \
  --require-artifact clip_embeddings \
  --require-artifact study_embeddings \
  --require-artifact structured_measurements \
  --require-artifact lvef_labels \
  --require-artifact split_map \
  --require-artifact multitask_panel \
  --output-dir "$PHASE1_AGGREGATE_DIR/artifact_audit" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/artifact_audit"

run_audit embedding_overlap \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/audit_embedding_eligibility_overlap.py \
  --selected-studies "$SELECTED_STUDIES" \
  --study-embeddings "$STUDY_EMBEDDING_MANIFEST" \
  "${OVERLAP_STAGE_ARGS[@]}" \
  --clip-embeddings "$MERGED_CLIP_MANIFEST" \
  "${OVERLAP_COMPLETENESS_ARGS[@]}" \
  --output-dir "$PHASE1_AGGREGATE_DIR/embedding_overlap" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/embedding_overlap"

run_audit selected_split_coverage \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/audit_subject_splits_and_denominators.py \
  --split-map "$SPLIT_MAP" \
  --exact-split-cohort "selected_studies=$SELECTED_STUDIES" \
  --output-dir "$PHASE1_AGGREGATE_DIR/selected_split_coverage" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/selected_split_coverage"

run_audit lvef_split_denominators \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/audit_subject_splits_and_denominators.py \
  --split-map "$SPLIT_MAP" \
  --cohort "lvef_labels=$LVEF_LABELS" \
  --cohort "vision_predictions=$LVEF_VISION_STUDY_PREDICTIONS" \
  --cohort "structured_predictions=$LVEF_STRUCTURED_PREDICTIONS" \
  --output-dir "$PHASE1_AGGREGATE_DIR/lvef_split_denominators" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/lvef_split_denominators"

run_audit multitask_split_denominators \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/audit_subject_splits_and_denominators.py \
  --split-map "$SPLIT_MAP" \
  --cohort "vision_predictions=$MT_VISION_PREDICTIONS" \
  --cohort "structured_predictions=$MT_STRUCTURED_PREDICTIONS" \
  --cohort "fusion_predictions=$MT_FUSION_PREDICTIONS" \
  --multitask-panel "$STRICT_PANEL" \
  --output-dir "$PHASE1_AGGREGATE_DIR/multitask_split_denominators" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/multitask_split_denominators"
```

The LVEF split audit intentionally omits fusion. Supplying the aggregate fusion comparison table as if it were patient-level predictions would be invalid.

`artifact_audit/denominator_summary.csv` reports raw source/store scope and must not be interpreted as the selected-cohort loss funnel. The selected-cohort funnel is `artifact_audit/selected_cohort_stage_reconciliation.csv`, which reports the selected intersection, outside-selected source/store studies, selected studies absent at every stage, and selected-scoped all-row completeness. `selected_cohort_stage_containment.csv` checks exact selected-study set containment through download, read, cine, extraction, clip embedding, and study aggregation, including all-artifact merged-clip versus study-store equality; it also requires the wide multitask panel to retain every selected base row. When Stage-D lineage is complete, `clip_embedding_component_union_provenance.csv` additionally requires the successful merged clip rows to equal the Stage-D-plus-nine-batch source union by clip key, multiplicity, non-index payload, subject/study sets, and study ownership. `selected_cohort_stage_subject_mapping.csv` fails on missing IDs, any study assigned to multiple subjects, or selected study-to-subject disagreement; exact discrepancy IDs remain restricted. `lvef_label_provenance.csv` reconstructs the builder's case-sensitive `measurement == "lvef"`, numeric `result`, median-by-`(subject_id, measurement_id)` semantics and rejects conflicting repeated manifest labels. `selected_cohort_integrity.csv` must confirm the one-study-per-subject policy, and `selected_split_coverage/exact_split_subject_coverage.csv` must confirm exact selected-subject versus split-map coverage.

### 7. Partial LVEF and strict multitask common-denominator dry runs

```bash
run_audit lvef_partial_common_denominators \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/build_common_evaluation_denominators.py \
  --modality "vision=$LVEF_VISION_STUDY_PREDICTIONS" \
  --modality "structured=$LVEF_STRUCTURED_PREDICTIONS" \
  --label-authority-csv "$LVEF_LABELS" \
  --label-column lvef \
  --binary-label-column lvef_binary_reduced \
  --binary-threshold 40 \
  --output-dir "$PHASE1_AGGREGATE_DIR/lvef_partial_common_denominators" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/lvef_partial_common_denominators"

run_audit multitask_common_denominators \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/build_common_evaluation_denominators.py \
  --modality "vision=$MT_VISION_PREDICTIONS" \
  --modality "structured=$MT_STRUCTURED_PREDICTIONS" \
  --modality "fusion=$MT_FUSION_PREDICTIONS" \
  --target-definition-csv "$STRICT_TASKS" \
  --target-panel-wide-csv "$STRICT_PANEL" \
  --target-panel-long-csv "$STRICT_PANEL_LONG" \
  --expected-target-count 29 \
  --label-column y_true \
  --output-dir "$PHASE1_AGGREGATE_DIR/multitask_common_denominators" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/multitask_common_denominators"
```

These commands compute unique row-key, subject-study ownership, subject-split, identifier-set, and label identity by task and split. They do not read prediction columns for performance analysis. Continuous values use a documented `rtol=1e-6`, `atol=1e-6` comparison to accommodate the historical runners' float32 label serialization. The LVEF audit requires both prediction modalities to match the collapsed study-level LVEF manifest authority, requires the binary endpoint to agree and equal `lvef < 40`, and remains explicitly partial—vision versus structured only. Do not run or claim a three-way LVEF identity result until a future authorized fusion-prediction regeneration exists. The multitask audit requires exact task and denominator reconciliation plus tolerance-aware label equality among the 29-row unique nonblank task definition, the wide and long frozen panel authorities, and all three prediction artifacts.

### 8. Train-only missingness and restricted dependency-registry expansion

```bash
run_audit train_missingness \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/analyze_measurement_missingness.py \
  --panel-csv "$STRICT_PANEL" \
  --analysis-split train \
  --min-pattern-count 10 \
  --output-dir "$PHASE1_AGGREGATE_DIR/train_missingness"

run_audit dependency_registry \
  "$LVEF_SCC_PYTHON_RESOLVED" scripts/build_target_dependency_registry.py \
  --historical-task-csv "$STRICT_TASKS" \
  --mapping-csv "$RAW_CANONICAL_MAPPING" \
  --task-metadata-csv "$STRICT_TASK_METADATA" \
  --output-registry-csv "$PHASE1_RESTRICTED_DIR/target_dependency_registry_raw_candidates.csv" \
  --output-evidence-csv "$PHASE1_AGGREGATE_DIR/leakage_evidence_matrix.csv"
```

The dependency registry contains raw measurement names and remains restricted. The evidence matrix is provisional and does not lock strict/pragmatic panels until clinical adjudication. No masking-panel command is part of this run because the authorized next block is limited to the audits listed above.

### 9. Aggregate packet safety gate

```bash
"$LVEF_SCC_PYTHON_RESOLVED" - "$PHASE1_AGGREGATE_DIR" <<'PY'
import csv
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
forbidden = {
    "subject_id", "patient_id", "person_id", "study_id", "dicom_study_id",
    "subject", "study", "identifier", "hadm_id", "stay_id", "mrn",
    "y_true", "y_pred", "label", "prediction", "pred_lvef",
    "pred_reduced_prob", "embedding", "path", "file_path", "absolute_path",
    "dicom_filepath", "dicom_abs_path", "keyframe_path", "npz_path",
}
issues = []

def inspect_json(value, relative, location="root"):
    if isinstance(value, dict):
        bad = sorted(str(key) for key in value if str(key).lower() in forbidden)
        if bad:
            issues.append((relative, location, bad))
        for key, child in value.items():
            inspect_json(child, relative, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            inspect_json(child, relative, f"{location}[{index}]")

for artifact in sorted(root.rglob("*")):
    if not artifact.is_file():
        continue
    relative = str(artifact.relative_to(root))
    if artifact.suffix.lower() in {".csv", ".tsv"}:
        delimiter = "\t" if artifact.suffix.lower() == ".tsv" else ","
        with artifact.open(newline="", errors="replace") as handle:
            columns = next(csv.reader(handle, delimiter=delimiter), [])
        bad = sorted(column for column in columns if column.lower() in forbidden)
        if bad:
            issues.append((relative, "header", bad))
    elif artifact.suffix.lower() == ".json":
        inspect_json(json.loads(artifact.read_text()), relative)
    elif artifact.suffix.lower() in {".npz", ".npy", ".parquet", ".pq"}:
        issues.append((relative, "forbidden_patient_level_file_type", []))

print(json.dumps({"n_files_scanned": sum(path.is_file() for path in root.rglob('*')), "n_issues": len(issues)}, sort_keys=True))
if issues:
    for relative, location, bad in issues:
        print(json.dumps({"file": relative, "location": location, "forbidden_keys_or_columns": bad}, sort_keys=True))
    raise SystemExit(2)
PY

portable_list_files "$PHASE1_AGGREGATE_DIR"
```

Do not transfer anything from `PHASE1_RESTRICTED_DIR`. Do not add either audit directory to Git.

## Safe outputs to paste back

Paste or attach only these aggregate outputs:

- `freeze_relative_file_inventory.txt`
- `batch_study_partition.json`
- `freeze_checksum_validation.tsv`
- `freeze_duplicate_pair_status.tsv`
- `stage_d_lineage_status.tsv`
- `artifact_schema_checksums.json`
- `audit_exit_status.tsv`
- `artifact_audit/artifact_inventory.csv`
- `artifact_audit/denominator_summary.csv`
- `artifact_audit/selected_cohort_integrity.csv`
- `artifact_audit/selected_cohort_stage_reconciliation.csv`
- `artifact_audit/selected_cohort_stage_containment.csv`
- `artifact_audit/selected_cohort_stage_subject_mapping.csv`
- `artifact_audit/lvef_label_provenance.csv`
- `artifact_audit/clip_embedding_component_union_provenance.csv`
- `artifact_audit/split_counts.csv`
- `artifact_audit/multitask_task_denominators.csv`
- `artifact_audit/audit_summary.json`
- `embedding_overlap/embedding_eligibility_overlap_summary.csv`
- `embedding_overlap/embedding_discrepancy_reason_counts.csv`
- `embedding_overlap/embedding_eligibility_overlap.summary.json`
- both split-audit aggregate directories
- `selected_split_coverage/exact_split_subject_coverage.csv` and its summary JSON
- the `lvef_partial_common_denominators` aggregate directory
- `multitask_common_denominators/common_denominator_audit.csv` and its summary JSON
- `multitask_common_denominators/target_set_reconciliation.csv`
- `multitask_common_denominators/target_definition_integrity.csv`
- the `train_missingness` aggregate directory
- `leakage_evidence_matrix.csv`
- the final aggregate packet safety-gate output

Also report the SCC Git commit shown by `git rev-parse HEAD`. Do not paste subject/study IDs, row-level labels or predictions, raw dependency registry rows, embeddings, DICOM paths, or anything from the restricted audit directory.

The then-unresolved freeze-manifest, clip-component, five-study attrition, and environment/checkpoint diagnostics are isolated in [SCC Phase 1B aggregate-only follow-up commands](scc_phase1b_followup_commands.md). Run that block only after reviewing the Phase 1A packet; it writes to a fresh Phase 1B audit root and has its own final allowlist gate. The completed follow-up is adjudicated in `phase1b_scc_followup_findings.md`: the 9,605 serialization/index findings and provisional five-study imaging-eligibility rule are no longer open scientific questions, while the 32 duplicate keys and historical environment linkage remain unresolved.

## Historical Phase 1B lock blockers

This checklist records the pre-follow-up gate. Where it conflicts with later findings, `phase1b_scc_followup_findings.md` and `phase1c_pre_revalidation_lock.md` control.

Do not proceed to confirmatory revalidation until all of the following are resolved:

- classify the failed SCC preservation-pack checksum at per-entry level and independently verify every input authority used for revalidation;
- reconcile the 32 duplicate clip-key excess rows on each side and 9,605 full-row non-index payload mismatches, including any normalization-only versus scientific-content distinction;
- classify the five selected studies that stop at cine candidacy and lock a principled exclusion, deterministic-reprocessing, sensitivity, or unresolved-missingness disposition;
- pass a pre-fit common-denominator dry run with zero subject, study, ownership, split, target-definition, and label-identity failures for every locked multitask target and modality;
- independently adjudicate and version raw aliases, formulas, dependency families, strict/pragmatic target panels, units, minimum support, thresholds, and clinical error/equivalence margins;
- lock the binary logistic penalty/C grid, compatible solver, class-weight rule, validation selection metric and tie-break, probability-calibration policy, and validation-only operating-point criterion;
- lock training-only structured feature-eligibility thresholds and missing-indicator creation, retention, and suppression rules, then pass the target-family masking audit;
- resolve or formally disposition historical checkpoint-to-embedding linkage and historical environment limitations, while fully specifying new-run checkpoint/environment capture;
- obtain separate owner authorization for any deterministic LVEF early-fusion prediction regeneration, which remains necessary for historical three-way paired LVEF denominators and deltas;
- version and checksum the final SAP/config with no post-test design change, pass the aggregate-only export gate, and record explicit owner authorization for the confirmatory run.

The selected Stage-D-plus-nine-batch partition, Stage-D lineage presence, one-study-per-subject integrity, selected-subject split structure, stage-level location of the five-study loss, and vision/structured LVEF identity are no longer listed as open gates; their passed or narrowed status is documented in `phase1a_scc_findings.md` and `phase1b_pre_revalidation_lock.md`.
