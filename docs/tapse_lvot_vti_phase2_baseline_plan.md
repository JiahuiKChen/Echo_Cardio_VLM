# Phase 2 Imaging-Only Baseline Plan: LVOT VTI First

## Reviewer-Facing Go/No-Go Memo

Phase 1 audit outputs support proceeding to Phase 2 imaging-only baselines, with LVOT VTI as the primary manuscript-ready target and TAPSE as a cautious secondary target. The primary Phase 2 denominator is the broader fullscale EchoPrime study-embedding cohort, not the ECHOVIEW-filtered subset. ECHOVIEW-filtered analyses should be reported as sensitivity analyses because ECHOVIEW is a derived view-classification subset rather than the full MIMIC-IV-ECHO DICOM corpus.

### LVOT VTI: Go As Primary Target

LVOT VTI has 3,787 numeric studies and 3,782 studies with processed EchoPrime study and clip embeddings. The distribution is clinically plausible overall: median 22 cm, IQR 18-26 cm, range 5-64 cm, with 93 values above the primary plausibility range and one hard extreme above 60 cm. The Phase 1 denominator funnel shows that ECHOVIEW is the bottleneck: only 387 LVOT VTI studies overlap ECHOVIEW, while 3,395 studies have embeddings but no ECHOVIEW labels.

Recommendation: proceed with continuous LVOT VTI regression from all-clips study embeddings as the primary Phase 2 analysis. Use ECHOVIEW A5C-or-other at threshold 0.70 as the leading view-filtered sensitivity analysis, with A5C-only and other-only as additional sensitivity/comparator analyses. Strict A5C-only should not be primary because LVOT VTI is Doppler-derived and ECHOVIEW's `prob_other` class includes Doppler, IV contrast, or unclassifiable clips.

Reviewer risk: medium. The target support and embedding coverage are strong; the main risk is clinical interpretability of all-clips embeddings for a Doppler measurement. This is mitigated by the ECHOVIEW Doppler-sensitive sensitivity analyses and by reporting all denominators separately.

### TAPSE: Conditional Go As Secondary Target

TAPSE has 1,134 numeric studies and 1,131 studies with processed EchoPrime study and clip embeddings. The distribution is plausible: median 20 mm, IQR 18-24 mm, range 5-35 mm, with no values outside the hard extreme range. However, only 115 TAPSE studies overlap ECHOVIEW, and the A4C-family threshold 0.70 sensitivity cohort contains only 100 studies.

Recommendation: run TAPSE as a secondary imaging-only baseline using all-clips study embeddings, but frame results cautiously. ECHOVIEW A4C-family analyses at threshold 0.70 should be sensitivity analyses only and are likely underpowered for a primary claim.

Reviewer risk: medium-high. TAPSE is clinically relevant and label support is adequate for an all-clips baseline, but ECHOVIEW does not identify RV-focused A4C, M-mode, or TAPSE-specific acquisition clips.

## Why 0.70 Is The Leading ECHOVIEW Sensitivity Threshold

The ECHOVIEW PhysioNet record describes a derived subset of MIMIC-IV-ECHO with 29,196 echocardiograms across 717 studies and 23 view-class probability columns. Its authors report manual expert review of A4C predictions at a 0.70 probability threshold, with higher thresholds recommended when greater precision is needed at the expense of recall. Source: [ECHOVIEW v0.1 PhysioNet](https://physionet.org/content/echoview/0.1/).

For this project, 0.70 is therefore a defensible leading sensitivity threshold, not a universal ground-truth cutoff. Thresholds 0.80, 0.90, and 0.95 remain sensitivity parameters and should be reported in denominator tables.

## Primary Phase 2 Design

Primary target: LVOT VTI, continuous regression in cm.

Primary input: fullscale all-clips EchoPrime study embeddings generated before Phase 2.

Primary split: existing deterministic subject-level split map `subject_split_map_v1.csv`.

Primary models:

- Null median baseline using the training-set target median.
- Ridge regression with standardized EchoPrime embeddings and a predeclared alpha grid selected by validation MAE.

Phase 2.1 stable-v2 reruns should use `docs/phase2_stable_imaging_baselines_runbook.md`, which defaults to a numerically stable Ridge solver (`svd`), an expanded alpha grid, explicit random seed metadata, captured warnings, and a separate output root.

Primary test metrics:

- MAE, RMSE, R2, median absolute error, and MAE normalized by training-set IQR.
- Bias and Bland-Altman limits of agreement.
- Pearson and Spearman correlation.
- Calibration slope and intercept from true value regressed on predicted value.
- Percentage of predictions within 2 cm and 3 cm for LVOT VTI.
- Subject-clustered bootstrap 95% confidence intervals on the held-out test set.

Secondary binary summaries for LVOT VTI:

- Low LVOT VTI below 18 cm.
- Low LVOT VTI below 20 cm.
- Sensitivity, specificity, PPV, NPV, F1, AUROC, average precision, and prevalence.

TAPSE secondary metrics should mirror the continuous framework in mm, with within-3 mm and within-5 mm accuracy and a binary threshold below 17 mm.

## Sensitivity Analyses

LVOT VTI sensitivity analyses:

- A5C-or-other ECHOVIEW-filtered study embeddings at threshold 0.70.
- Other-only ECHOVIEW-filtered study embeddings at threshold 0.70.
- A5C-only ECHOVIEW-filtered study embeddings at threshold 0.70.
- Optional threshold ladder at 0.80, 0.90, and 0.95 if sample size remains adequate.
- Optional pooling comparison: mean pooling, probability-weighted mean, and top-k mean.

TAPSE sensitivity analyses:

- A4C-family ECHOVIEW-filtered study embeddings at threshold 0.70.
- A4C-family plus RV inflow at threshold 0.70.
- Optional threshold ladder at 0.80, 0.90, and 0.95 only as feasibility permits.

ECHOVIEW-filtered sensitivity results must not be interpreted as the full target-view cohort in MIMIC-IV-ECHO. Low ECHOVIEW overlap primarily reflects limited ECHOVIEW availability.

## Leakage Boundary

Phase 2 imaging-only baselines must use EchoPrime embeddings only. Structured measurements are used only to construct the target and must not enter the feature matrix. Tabular or fusion comparators are deferred until target-specific leakage exclusions are reviewed and encoded.

Direct leakage exclusions already identified:

- LVOT VTI: `lvot_vti`, `av_vti`.
- TAPSE: `tapse`, `rv_function`.

Manual-review fields include AV/LVOT velocity and gradient fields for LVOT VTI, and RV/tricuspid fields for TAPSE.

## Restricted Versus Manuscript-Safe Outputs

Restricted patient-level outputs:

- View-filtered embedding NPZs.
- View-filtered embedding manifests.
- Per-study prediction CSVs.

Aggregate outputs that may become manuscript-safe after review:

- Continuous metrics CSV.
- Binary metrics CSV.
- Ridge alpha-selection CSV.
- Bootstrap confidence-interval CSV.
- Baseline summary JSON with counts and output paths.

Do not commit restricted outputs to the repo.

## SCC Commands

Primary LVOT VTI all-clips baseline:

```bash
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
PY=.venv-echoprime/bin/python
OUT=/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2

$PY scripts/run_tapse_lvot_vti_imaging_baseline.py \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embeddings_512.npz \
  --study-embedding-manifest outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embedding_manifest.csv \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --target lvot_vti \
  --analysis-label all_clips_study_embeddings \
  --output-dir $OUT/lvot_vti/all_clips
```

Secondary TAPSE all-clips baseline:

```bash
$PY scripts/run_tapse_lvot_vti_imaging_baseline.py \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embeddings_512.npz \
  --study-embedding-manifest outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embedding_manifest.csv \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --target tapse \
  --analysis-label all_clips_study_embeddings \
  --output-dir $OUT/tapse/all_clips
```

LVOT VTI ECHOVIEW A5C-or-other threshold 0.70 sensitivity:

```bash
mkdir -p $OUT/view_filtered_embeddings

$PY scripts/aggregate_view_filtered_embeddings.py \
  --clip-embedding-npz outputs/cloud_cohorts/fullscale_all/merged_clip_embeddings_512/clip_embeddings_512.npz \
  --joined-clip-manifest-csv /restricted/project/mimicecho/outputs/tapse_lvot_vti_phase1/echoview_join/echoview_joined_clip_manifest.csv \
  --view-policy a5c_or_other \
  --threshold 0.70 \
  --pooling mean \
  --output-npz $OUT/view_filtered_embeddings/lvot_vti_a5c_or_other_0.70_mean.npz \
  --output-manifest $OUT/view_filtered_embeddings/lvot_vti_a5c_or_other_0.70_mean_manifest.csv

$PY scripts/run_tapse_lvot_vti_imaging_baseline.py \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz $OUT/view_filtered_embeddings/lvot_vti_a5c_or_other_0.70_mean.npz \
  --study-embedding-manifest $OUT/view_filtered_embeddings/lvot_vti_a5c_or_other_0.70_mean_manifest.csv \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --target lvot_vti \
  --analysis-label echoview_a5c_or_other_0.70_mean \
  --output-dir $OUT/lvot_vti/echoview_a5c_or_other_0.70_mean
```

TAPSE ECHOVIEW A4C-family threshold 0.70 sensitivity:

```bash
$PY scripts/aggregate_view_filtered_embeddings.py \
  --clip-embedding-npz outputs/cloud_cohorts/fullscale_all/merged_clip_embeddings_512/clip_embeddings_512.npz \
  --joined-clip-manifest-csv /restricted/project/mimicecho/outputs/tapse_lvot_vti_phase1/echoview_join/echoview_joined_clip_manifest.csv \
  --view-policy a4c_family \
  --threshold 0.70 \
  --pooling mean \
  --output-npz $OUT/view_filtered_embeddings/tapse_a4c_family_0.70_mean.npz \
  --output-manifest $OUT/view_filtered_embeddings/tapse_a4c_family_0.70_mean_manifest.csv

$PY scripts/run_tapse_lvot_vti_imaging_baseline.py \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz $OUT/view_filtered_embeddings/tapse_a4c_family_0.70_mean.npz \
  --study-embedding-manifest $OUT/view_filtered_embeddings/tapse_a4c_family_0.70_mean_manifest.csv \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --target tapse \
  --analysis-label echoview_a4c_family_0.70_mean \
  --output-dir $OUT/tapse/echoview_a4c_family_0.70_mean
```

## Go/No-Go Criteria After Baseline Results

Proceed toward manuscript modeling if:

- LVOT VTI Ridge improves materially over the null median baseline on held-out test MAE and MAE/IQR.
- Test confidence intervals exclude a trivial or harmful effect relative to null.
- Errors are clinically plausible and not driven only by outliers above the primary plausibility range.
- Subject-level split integrity remains clean.
- ECHOVIEW sensitivity analyses are directionally consistent with all-clips results, while being interpreted as subset analyses.

Defer, revise, or stop if:

- The all-clips model does not beat the null median baseline.
- Results are only positive in a tiny ECHOVIEW-filtered subset.
- Performance depends on a threshold chosen after seeing test metrics.
- Predictions show clinically unacceptable bias or very wide Bland-Altman limits.
- Any patient-level outputs appear inside the repo or any split leakage is detected.
