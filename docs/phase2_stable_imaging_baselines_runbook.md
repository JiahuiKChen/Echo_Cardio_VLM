# Phase 2.1 Stable Imaging Baselines Runbook

## Purpose

The first LVOT VTI all-clips baseline showed a promising imaging signal, but sklearn emitted Ridge ill-conditioned matrix warnings with the default solver path. Phase 2.1 reruns the imaging-only baselines with a numerically stable Ridge configuration before manuscript numbers are frozen or sensitivity runs are interpreted.

Stable-v2 keeps the same scientific framing:

- LVOT VTI is the primary Phase 2 target.
- TAPSE is a cautious secondary target.
- The fullscale all-clips study-embedding cohort is the primary modeling denominator.
- ECHOVIEW-filtered cohorts are sensitivity analyses, not the primary denominator.

## Output Governance

Patient-level restricted outputs:

- `imaging_baseline_predictions.csv`
- view-filtered embedding `.npz` files
- view-filtered embedding manifests

Aggregate outputs that may be manuscript-review candidates:

- `imaging_baseline_metrics.csv`
- `imaging_baseline_binary_metrics.csv`
- `imaging_baseline_ridge_alpha_selection.csv`
- `imaging_baseline_bootstrap_ci.csv`
- `imaging_baseline_warnings.json`
- `imaging_baseline_summary.json`

Patient-level predictions are written only when `--allow-restricted-patient-outputs` is supplied and the output directory is outside the repo, unless explicitly running synthetic tests.

## SCC Setup

```bash
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
git fetch origin
git checkout codex/phase2-stable-imaging-baselines
git pull origin codex/phase2-stable-imaging-baselines

PY=.venv-echoprime/bin/python
OUT=/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2
ALPHAS=0.01,0.03,0.1,0.3,1,3,10,30,100,300,1000
SEED=1337
mkdir -p "$OUT/logs"
set -o pipefail
```

## LVOT VTI All-Clips Stable-v2

```bash
time $PY scripts/run_tapse_lvot_vti_imaging_baseline.py \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embeddings_512.npz \
  --study-embedding-manifest outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embedding_manifest.csv \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --target lvot_vti \
  --analysis-label all_clips_study_embeddings_stable_v2 \
  --ridge-solver svd \
  --standardize-features \
  --ridge-alphas "$ALPHAS" \
  --random-seed "$SEED" \
  --n-bootstrap 2000 \
  --allow-restricted-patient-outputs \
  --output-dir "$OUT/lvot_vti/all_clips" \
  2>&1 | tee "$OUT/logs/lvot_vti_all_clips_stable_v2.log"
echo "exit_status=${PIPESTATUS[0]}"
```

## LVOT VTI Hard-Extreme Sensitivity

```bash
time $PY scripts/run_tapse_lvot_vti_imaging_baseline.py \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embeddings_512.npz \
  --study-embedding-manifest outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embedding_manifest.csv \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --target lvot_vti \
  --analysis-label all_clips_study_embeddings_stable_v2_exclude_hard_extremes \
  --ridge-solver svd \
  --standardize-features \
  --ridge-alphas "$ALPHAS" \
  --random-seed "$SEED" \
  --n-bootstrap 2000 \
  --exclude-hard-extremes \
  --allow-restricted-patient-outputs \
  --output-dir "$OUT/lvot_vti/all_clips_exclude_hard_extremes" \
  2>&1 | tee "$OUT/logs/lvot_vti_all_clips_exclude_hard_extremes_stable_v2.log"
echo "exit_status=${PIPESTATUS[0]}"
```

## View-Filtered Sensitivities

Create view-filtered embeddings first, then run the baseline script against the generated study embeddings.

Example for LVOT VTI A5C-or-other at threshold 0.70:

```bash
mkdir -p "$OUT/view_filtered_embeddings"

$PY scripts/aggregate_view_filtered_embeddings.py \
  --clip-embedding-npz outputs/cloud_cohorts/fullscale_all/merged_clip_embeddings_512/clip_embeddings_512.npz \
  --joined-clip-manifest-csv /restricted/project/mimicecho/outputs/tapse_lvot_vti_phase1/echoview_join/echoview_joined_clip_manifest.csv \
  --view-policy a5c_or_other \
  --threshold 0.70 \
  --pooling mean \
  --output-npz "$OUT/view_filtered_embeddings/lvot_vti_a5c_or_other_0.70_mean.npz" \
  --output-manifest "$OUT/view_filtered_embeddings/lvot_vti_a5c_or_other_0.70_mean_manifest.csv"

time $PY scripts/run_tapse_lvot_vti_imaging_baseline.py \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz "$OUT/view_filtered_embeddings/lvot_vti_a5c_or_other_0.70_mean.npz" \
  --study-embedding-manifest "$OUT/view_filtered_embeddings/lvot_vti_a5c_or_other_0.70_mean_manifest.csv" \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --target lvot_vti \
  --analysis-label echoview_a5c_or_other_0.70_mean_stable_v2 \
  --ridge-solver svd \
  --standardize-features \
  --ridge-alphas "$ALPHAS" \
  --random-seed "$SEED" \
  --n-bootstrap 2000 \
  --allow-restricted-patient-outputs \
  --output-dir "$OUT/lvot_vti/echoview_a5c_or_other_0.70_mean" \
  2>&1 | tee "$OUT/logs/lvot_vti_echoview_a5c_or_other_0.70_mean_stable_v2.log"
echo "exit_status=${PIPESTATUS[0]}"
```

Repeat with `--view-policy other` for other-only, `--view-policy a5c` for A5C-only, or thresholds `0.80`, `0.90`, and `0.95` for the A5C-or-other threshold ladder.

## TAPSE All-Clips Stable-v2

```bash
time $PY scripts/run_tapse_lvot_vti_imaging_baseline.py \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embeddings_512.npz \
  --study-embedding-manifest outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embedding_manifest.csv \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --target tapse \
  --analysis-label all_clips_study_embeddings_stable_v2 \
  --ridge-solver svd \
  --standardize-features \
  --ridge-alphas "$ALPHAS" \
  --random-seed "$SEED" \
  --n-bootstrap 2000 \
  --allow-restricted-patient-outputs \
  --output-dir "$OUT/tapse/all_clips" \
  2>&1 | tee "$OUT/logs/tapse_all_clips_stable_v2.log"
echo "exit_status=${PIPESTATUS[0]}"
```

## TAPSE Hard-Extreme Sensitivity

```bash
time $PY scripts/run_tapse_lvot_vti_imaging_baseline.py \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embeddings_512.npz \
  --study-embedding-manifest outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embedding_manifest.csv \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --target tapse \
  --analysis-label all_clips_study_embeddings_stable_v2_exclude_hard_extremes \
  --ridge-solver svd \
  --standardize-features \
  --ridge-alphas "$ALPHAS" \
  --random-seed "$SEED" \
  --n-bootstrap 2000 \
  --exclude-hard-extremes \
  --allow-restricted-patient-outputs \
  --output-dir "$OUT/tapse/all_clips_exclude_hard_extremes" \
  2>&1 | tee "$OUT/logs/tapse_all_clips_exclude_hard_extremes_stable_v2.log"
echo "exit_status=${PIPESTATUS[0]}"
```
