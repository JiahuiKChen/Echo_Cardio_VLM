# Manuscript Results (Camera-Ready Draft)

## Results

### Cohort derivation and evaluation population

The fullscale SCC pipeline selected 4,530 eligible MIMIC-IV-ECHO studies under a one-study-per-subject policy with fixed subject-level splits. Study-level embeddings were available for 4,696 studies because the merged embedding manifest included prior processed studies beyond the current eligibility table. The final LVEF evaluation set contained 2,833 studies after intersecting eligibility, structured-measurement linkage, and embedding-linked keyframe manifests. The held-out test split contained 426 studies.

Integrity checks showed complete coverage of the evaluation intersection (LVEF-labeled studies missing in study embeddings: 0). Postrun audit reported zero missing required files and zero structural warnings.

### Primary endpoint performance (LVEF prediction)

On the held-out test set (n=426), the vision-only baseline (E2b) achieved AUC 0.9581, R2 0.6127, and MAE 5.6761 EF points. The structured tabular baseline (E3; leakage-filtered) achieved AUC 0.9153, R2 0.4383, and MAE 6.6842. The multimodal fusion model (E5, concatenated vision+tabular with linear heads) achieved AUC 0.9618, R2 0.6615, and MAE 5.3935.

Relative to E2b, E5 improved AUC by +0.0038, R2 by +0.0489, and MAE by -0.2826 EF points. E3 underperformed E2b across all primary metrics.

### Uncertainty analysis and tabular feature audit

In the 10,000-bootstrap rerun, test AUC estimates were: E2b 0.9581 (95% CI 0.9334-0.9784), E3 0.9153 (95% CI 0.8734-0.9513), and E5 0.9618 (95% CI 0.9402-0.9797), supporting stable multimodal gains versus tabular-only performance and a modest improvement versus vision-only performance.

The structured export contained 669,378 rows and 186 unique measurement names. Leakage control removed 4 LVEF-related measurement types, coverage filtering removed 23 low-coverage types, and 51 features were retained for E3/E5 modeling. Mean retained-feature missingness in E3 was 0.43.

## Table Captions and Values

### Table 1. Cohort derivation and modality-availability counts

| Stage | n studies |
|---|---:|
| Eligible studies | 4,530 |
| Study embeddings available | 4,696 |
| LVEF evaluation cohort | 2,833 |
| LVEF ∩ study embeddings | 2,833 |
| LVEF missing in study embeddings | 0 |

### Table 2. Primary test-set performance by experiment

| Experiment | Test n | AUC | R2 | MAE (EF points) | ΔAUC vs E2b | ΔR2 vs E2b | ΔMAE vs E2b |
|---|---:|---:|---:|---:|---:|---:|---:|
| E2b vision-only | 426 | 0.9581 | 0.6127 | 5.6761 | 0.0000 | 0.0000 | 0.0000 |
| E3 tabular-only | 426 | 0.9153 | 0.4383 | 6.6842 | -0.0428 | -0.1744 | +1.0081 |
| E5 fusion (concat+linear) | 426 | 0.9618 | 0.6615 | 5.3935 | +0.0038 | +0.0489 | -0.2826 |

### Table 3. Structured-measurement curation and leakage audit

| Item | Value |
|---|---:|
| Structured rows exported | 669,378 |
| Unique measurement names (raw) | 186 |
| Excluded as LVEF leakage | 4 |
| Excluded low coverage | 23 |
| Retained tabular features | 51 |
| Mean missing rate (retained features) | 0.43 |

## Figure Legends

### Figure 1. Primary performance comparison across vision, tabular, and multimodal models

Bar-plot panel comparing test AUC, test R2, and test MAE for E2b (vision-only), E3 (tabular-only), and E5 (fusion). All metrics are computed on the same held-out test split (n=426 studies). The fusion model provides the strongest overall performance, with highest AUC and R2 and lowest MAE.

### Figure 2. Test AUC with 95% bootstrap confidence intervals (10,000 resamples)

Point estimates and percentile bootstrap confidence intervals for test AUC across E2b, E3, and E5. Confidence intervals were computed from 10,000 bootstrap resamples of the held-out test set. E5 and E2b show overlapping high-AUC intervals, while both outperform E3.

## Sources of Truth

- Fullscale audit report: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/audit_postrun/fullscale_audit_report.md`
- Primary reporting assets: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all/reporting_assets/`
- Frozen evidence pack: `/restricted/project/mimicecho/outputs/freeze_fullscale_20260331_111135`

