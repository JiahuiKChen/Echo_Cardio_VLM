# Manuscript Results Snapshot

- Generated at UTC: `2026-04-01T22:24:03.338751+00:00`
- Snapshot dir: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/docs/results_snapshot/2026-04-01_fullscale`
- SCC fullscale root: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all`
- SCC audit source: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/audit_postrun/fullscale_audit_summary.json`
- SCC primary assets source: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/reporting_assets`
- SCC multitask assets source: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/reporting_multitask_assets`
- SCC freeze pack: `/restricted/project/mimicecho/outputs/freeze_fullscale_20260331_111135`

## Methodology Summary

We executed an SCC-first, reproducible pipeline on MIMIC-IV-ECHO with deterministic subject-level splitting and one study per subject. Multi-frame DICOM cine clips were encoded with EchoPrime (encoder-only, 512-dimensional embeddings) and aggregated to study level. Structured measurements were exported and curated with leakage controls. Primary analysis evaluated LVEF prediction using vision-only, structured-measurement-only, and multimodal fusion models. Secondary analysis evaluated a strict multitask panel of quantitative echocardiographic measurements.

## Cohort Summary

- Eligible studies: `4530`
- Study embeddings available: `4696`
- LVEF evaluation cohort: `2833`
- LVEF ∩ study embeddings: `2833`
- LVEF missing in study embeddings: `0`
- Structured measurement rows: `669378`
- Unique structured measurement names: `186`

## Primary Endpoint Results

- E2b vision-only: AUC 0.9581, R2 0.6127, MAE 5.6761, test n 426
- E3 tabular-only: AUC 0.9153, R2 0.4383, MAE 6.6842, test n 426
- E5 fusion (concat+linear): AUC 0.9618, R2 0.6615, MAE 5.3935, test n 426

## Multitask Summary Results

- vision: tasks 29, mean R2 0.3196, median R2 0.3579, mean MAE/IQR 0.4953
- tabular: tasks 29, mean R2 0.4385, median R2 0.3920, mean MAE/IQR 0.4191
- fusion: tasks 29, mean R2 0.4886, median R2 0.5576, mean MAE/IQR 0.3973

## Included Files

- `audit/fullscale_audit_summary.json`
- `audit/fullscale_audit_report.md`
- `primary/table_1_cohort_counts.csv`
- `primary/table_2_primary_metrics.csv`
- `primary/table_3_tabular_feature_audit.csv`
- `primary/results_summary.json`
- `primary/figure_1_primary_metrics.png`
- `primary/figure_2_auc_with_bootstrap_ci.png`
- `multitask/multitask_macro_summary.csv`
- `multitask/multitask_task_level_comparison.csv`
- `multitask/multitask_win_counts.csv`
- `multitask/multitask_top_fusion_gains.csv`
- `multitask/multitask_results_summary.json`
- `multitask/figure_multitask_macro.png`
- `multitask/figure_multitask_fusion_gain_hist.png`

## Regeneration Commands

```bash
PY=.venv-echoprime/bin/python
$PY scripts/audit_fullscale_outputs.py --fullscale-root /restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all --output-dir /restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/audit_postrun --strict
$PY scripts/generate_fullscale_results_assets.py --fullscale-root /restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all --output-dir /restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/reporting_assets --e5-bootstrap-metrics /restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/eval_e5_fusion_boot10k/fusion_metrics.json
$PY scripts/generate_multitask_results_assets.py --fullscale-root /restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all --vision-dir eval_multitask_vision_strict --tabular-dir eval_multitask_tabular_strict --fusion-dir eval_multitask_fusion_strict --output-dir /restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/reporting_multitask_assets
```
