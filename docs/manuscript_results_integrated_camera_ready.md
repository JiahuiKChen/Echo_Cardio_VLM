# Manuscript Results (Integrated LVEF + Multitask; Camera-Ready Draft)

## Methods Summary

We executed an SCC-first, reproducible pipeline on MIMIC-IV-ECHO with deterministic cohorting and subject-level train/val/test splits. Cohort selection yielded 4,530 eligible studies (one study per subject). DICOM cine clips were processed and encoded with EchoPrime (encoder-only, 512-d), then aggregated to study-level embeddings. Structured measurements were exported from linked measurement IDs and used for both single-endpoint and multitask analyses.

For primary endpoint modeling, we evaluated LVEF prediction on the intersection of eligibility, structured-measurement linkage, and embedding/keyframe linkage (n=2,833 studies; test n=426). We compared:
- E2b: vision-only (study embeddings)
- E3: tabular-only structured measurements (LVEF-leakage-controlled)
- E5: fusion (vision + tabular)

For multitask expansion, we canonicalized 186 raw measurement names into 178 grouped tasks, then applied support and quality filters to construct:
- broad panel: 38 tasks
- strict panel (known preferred units): 29 tasks

Multitask baselines used per-task regression with consistent data sufficiency thresholds (min train/val/test/total labeled samples per task) and leave-one-task-out tabular features for tabular/fusion runs to avoid identity leakage from the target variable itself.

## Results

### Primary LVEF Endpoint

On the held-out test set (n=426 studies), LVEF performance was:
- E2b (vision): AUC 0.9581, R2 0.6127, MAE 5.6761 EF points
- E3 (tabular): AUC 0.9153, R2 0.4383, MAE 6.6842
- E5 (fusion): AUC 0.9618, R2 0.6615, MAE 5.3935

Relative to E2b, fusion improved AUC by +0.0038, R2 by +0.0489, and MAE by -0.2826 EF points. In the 10,000-bootstrap rerun, test AUC confidence intervals were:
- E2b: 0.9334–0.9784
- E3: 0.8734–0.9513
- E5: 0.9402–0.9797

### Multitask Structured-Measurement Expansion (Strict 29-task Panel)

Macro test performance across 29 tasks:
- Vision-only: mean R2 0.3196, median R2 0.3579, mean MAE/IQR 0.4953
- Tabular-only: mean R2 0.4385, median R2 0.3920, mean MAE/IQR 0.4191
- Fusion: mean R2 0.4886, median R2 0.5576, mean MAE/IQR 0.3973

These results show:
1. Tabular-only outperformed vision-only on aggregate multitask regression.
2. Fusion provided the strongest overall multitask performance, improving both R2 and normalized error beyond either unimodal baseline.

Representative high-performing fusion tasks included fractional shortening (`task__fs`), TR-derived pressure/velocity tasks (`task__tr_mmhg`, `task__tricuspid_regurgitant_peak_velocity`), LV dimensions, and selected Doppler/valvular measurements.

### Cohort Integrity and Reproducibility

Postrun audits reported zero missing required files and zero structural warnings for the fullscale run. The evaluation intersection for LVEF was complete (no LVEF-labeled studies missing study embeddings). All key metrics/tables/figures were generated from SCC-resident artifacts with checksum-tracked frozen evidence bundles.

## Table/Figure Pointers

Primary endpoint assets:
- `outputs/cloud_cohorts/fullscale_all/reporting_assets/`

Multitask assets:
- `outputs/cloud_cohorts/fullscale_all/reporting_multitask_assets/`

Frozen evidence:
- `/restricted/project/mimicecho/outputs/freeze_fullscale_20260331_111135`

