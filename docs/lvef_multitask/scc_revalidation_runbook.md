# SCC Phase 1A audit runbook

This runbook inspects existing artifacts and performs denominator, split, dependency, masking, and missingness dry checks. It does not fit a model or compute new confirmatory test performance.

## 1. Create or update the dedicated SCC worktree

Use the existing SCC repository as the Git object source, but do not overwrite its active checkout.

```bash
REPO_SOURCE=/restricted/project/mimicecho/code/Echo_Cardio_VLM
LVEF_WORKTREE=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
LVEF_BRANCH=codex/lvef-multitask-revalidation

git -C "$REPO_SOURCE" fetch origin --prune

if [ -d "$LVEF_WORKTREE" ]; then
  git -C "$LVEF_WORKTREE" status --short
  git -C "$LVEF_WORKTREE" switch "$LVEF_BRANCH"
  git -C "$LVEF_WORKTREE" pull --ff-only origin "$LVEF_BRANCH"
else
  git -C "$REPO_SOURCE" worktree add "$LVEF_WORKTREE" "$LVEF_BRANCH"
fi

git -C "$LVEF_WORKTREE" status --short
git -C "$LVEF_WORKTREE" branch --show-current
git -C "$LVEF_WORKTREE" log -1 --oneline --decorate
git -C "$LVEF_WORKTREE" merge-base --is-ancestor 23c74cc "$LVEF_BRANCH"
```

Stop if the worktree is dirty, the branch differs, or the merge-base check fails.

## 2. Define restricted artifact paths

The values below are explicit placeholders. Resolve them by read-only inspection of the existing fullscale directory; do not copy restricted data into Git.

```bash
PHASE1_ARTIFACT_ROOT=/restricted/project/mimicecho/artifacts/fullscale
PHASE1_AUDIT_ROOT=/restricted/project/mimicecho/audits/lvef_multitask_phase1a_20260801
PHASE1_AGGREGATE_DIR="$PHASE1_AUDIT_ROOT/aggregate"
PHASE1_RESTRICTED_DIR="$PHASE1_AUDIT_ROOT/restricted"

PUBLIC_RECORDS="$PHASE1_ARTIFACT_ROOT/public_mimic_echo_records.csv"
ELIGIBLE_ALL="$PHASE1_ARTIFACT_ROOT/all_eligible_studies_before_subject_selection.csv"
SELECTED_STUDIES="$PHASE1_ARTIFACT_ROOT/all_eligible_studies.csv"
DOWNLOADED_STUDIES="$PHASE1_ARTIFACT_ROOT/downloaded_studies_manifest.csv"
READABLE_DICOMS="$PHASE1_ARTIFACT_ROOT/dicom_audit_manifest.csv"
EXTRACTED_CLIPS="$PHASE1_ARTIFACT_ROOT/merged_clip_manifest.csv"
CLIP_EMBEDDINGS="$PHASE1_ARTIFACT_ROOT/merged_clip_embeddings_manifest.csv"
STUDY_EMBEDDINGS="$PHASE1_ARTIFACT_ROOT/study_embeddings_manifest.csv"
STRUCTURED_MEASUREMENTS="$PHASE1_ARTIFACT_ROOT/structured_measurements.csv"
LVEF_LABELS="$PHASE1_ARTIFACT_ROOT/lvef_still_manifest.csv"
SPLIT_MAP="$PHASE1_ARTIFACT_ROOT/subject_split_map_v1.csv"
MULTITASK_PANEL="$PHASE1_ARTIFACT_ROOT/multitask_panel_wide.csv"
RAW_CANONICAL_MAPPING="$PHASE1_ARTIFACT_ROOT/measurement_to_canonical_mapping.csv"
TASK_METADATA="$PHASE1_ARTIFACT_ROOT/multitask_task_metadata.csv"
VISION_PREDICTIONS="$PHASE1_ARTIFACT_ROOT/vision_predictions_restricted.csv"
STRUCTURED_PREDICTIONS="$PHASE1_ARTIFACT_ROOT/structured_predictions_restricted.csv"
FUSION_PREDICTIONS="$PHASE1_ARTIFACT_ROOT/fusion_predictions_restricted.csv"

mkdir -p "$PHASE1_AGGREGATE_DIR" "$PHASE1_RESTRICTED_DIR"
cd "$LVEF_WORKTREE"
```

Before running audits, use `find "$PHASE1_ARTIFACT_ROOT" -maxdepth 3 -type f` and update only the variables whose historical filenames differ. Save the resolved path list under the restricted audit directory.

## 3. Inventory artifacts and aggregate denominators

```bash
python3 scripts/audit_lvef_multitask_artifacts.py \
  --public-records "$PUBLIC_RECORDS" \
  --eligible-studies "$ELIGIBLE_ALL" \
  --selected-studies "$SELECTED_STUDIES" \
  --downloaded-studies "$DOWNLOADED_STUDIES" \
  --readable-dicoms "$READABLE_DICOMS" \
  --extracted-clips "$EXTRACTED_CLIPS" \
  --clip-embeddings "$CLIP_EMBEDDINGS" \
  --study-embeddings "$STUDY_EMBEDDINGS" \
  --structured-measurements "$STRUCTURED_MEASUREMENTS" \
  --lvef-labels "$LVEF_LABELS" \
  --split-map "$SPLIT_MAP" \
  --multitask-panel "$MULTITASK_PANEL" \
  --output-dir "$PHASE1_AGGREGATE_DIR/artifact_audit" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/artifact_audit"
```

## 4. Reconcile selected and embedded studies

```bash
python3 scripts/audit_embedding_eligibility_overlap.py \
  --selected-studies "$SELECTED_STUDIES" \
  --eligible-all-studies "$ELIGIBLE_ALL" \
  --study-embeddings "$STUDY_EMBEDDINGS" \
  --downloaded-studies "$DOWNLOADED_STUDIES" \
  --readable-dicoms "$READABLE_DICOMS" \
  --extracted-clips "$EXTRACTED_CLIPS" \
  --clip-embeddings "$CLIP_EMBEDDINGS" \
  --output-dir "$PHASE1_AGGREGATE_DIR/embedding_overlap" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/embedding_overlap"
```

A nonzero exit is expected when discrepancies exist; inspect the restricted reason file rather than suppressing the failure.

## 5. Audit split integrity and denominators

```bash
python3 scripts/audit_subject_splits_and_denominators.py \
  --split-map "$SPLIT_MAP" \
  --cohort lvef_labels="$LVEF_LABELS" \
  --cohort vision_predictions="$VISION_PREDICTIONS" \
  --cohort structured_predictions="$STRUCTURED_PREDICTIONS" \
  --cohort fusion_predictions="$FUSION_PREDICTIONS" \
  --multitask-panel "$MULTITASK_PANEL" \
  --output-dir "$PHASE1_AGGREGATE_DIR/split_denominators" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/split_denominators"
```

## 6. Expand the dependency candidate registry from raw SCC mappings

The generated registry may contain raw names and therefore remains restricted until an aggregate-safe reviewed version is prepared.

```bash
python3 scripts/build_target_dependency_registry.py \
  --historical-task-csv docs/results_snapshot/2026-04-01_fullscale/multitask/multitask_task_level_comparison.csv \
  --mapping-csv "$RAW_CANONICAL_MAPPING" \
  --task-metadata-csv "$TASK_METADATA" \
  --output-registry-csv "$PHASE1_RESTRICTED_DIR/target_dependency_registry_raw_candidates.csv" \
  --output-evidence-csv "$PHASE1_AGGREGATE_DIR/leakage_evidence_matrix.csv"
```

## 7. Common-denominator dry check

```bash
python3 scripts/build_common_evaluation_denominators.py \
  --modality vision="$VISION_PREDICTIONS" \
  --modality structured="$STRUCTURED_PREDICTIONS" \
  --modality fusion="$FUSION_PREDICTIONS" \
  --label-column y_true \
  --output-dir "$PHASE1_AGGREGATE_DIR/common_denominators" \
  --restricted-output-dir "$PHASE1_RESTRICTED_DIR/common_denominators"
```

This command must not be interpreted as performance analysis. It inspects IDs and labels only.

## 8. Masking and missingness dry checks

```bash
python3 scripts/build_masked_report_completion_panel.py \
  --panel-csv "$MULTITASK_PANEL" \
  --registry-csv "$PHASE1_RESTRICTED_DIR/target_dependency_registry_raw_candidates.csv" \
  --mode both \
  --output-dir "$PHASE1_AGGREGATE_DIR/masking_dry_run"

python3 scripts/analyze_measurement_missingness.py \
  --panel-csv "$MULTITASK_PANEL" \
  --output-dir "$PHASE1_AGGREGATE_DIR/missingness" \
  --min-pattern-count 10
```

## 9. Review aggregate outputs

```bash
find "$PHASE1_AGGREGATE_DIR" -type f -maxdepth 3 -print
find "$PHASE1_RESTRICTED_DIR" -type f -maxdepth 3 -print
```

Before transferring any aggregate packet off SCC, verify that it contains no subject/study IDs, predictions, labels, embeddings, paths, or low-count unsuppressed row patterns.

## 10. Future confirmatory revalidation placeholder

Do **not** run model revalidation yet. The future command will be added only after:

- denominator and split audits pass;
- raw dependencies and units are clinically adjudicated;
- strict/pragmatic masks are frozen;
- the SAP/config amendment is checksummed;
- the confirmatory run is explicitly authorized.

## Outputs to return for review

Paste or attach only the aggregate directory plus:

- a redacted list of artifact filenames and checksums;
- counts by restricted warning reason;
- confirmation that restricted identifier files remain on SCC;
- environment and EchoPrime checkpoint/release checksums;
- any audit traceback or nonzero exit summary.

Do not paste subject IDs, study IDs, target values, predictions, embeddings, or DICOM paths.
