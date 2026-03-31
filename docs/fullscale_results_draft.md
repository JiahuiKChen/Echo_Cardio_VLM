# Fullscale Results Draft (E2b / E3 / E5)

This draft is aligned to the SCC fullscale run at:
`/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all`
with frozen artifacts at:
`/restricted/project/mimicecho/outputs/freeze_fullscale_20260331_111135`.

## Results

### Cohort construction and evaluation set

The fullscale cohort selection produced 4,530 eligible studies (one study per subject under the fixed subject-level split policy). Study-level embedding artifacts were available for 4,696 studies due to inclusion of prior processed batches; this included 171 studies not in the current eligible manifest and 5 eligible studies without embeddings. For the target LVEF task, the final evaluation cohort included 2,833 studies after intersection of eligibility, structured-measurement linkage, and embedding-linked keyframe manifest constraints. No LVEF-labeled studies were missing from study embeddings in the final evaluation intersection (2,833/2,833).

### Primary endpoint performance (LVEF prediction)

On the held-out test set (n=426 studies), the vision-only baseline (E2b) achieved AUC 0.9581, R2 0.6127, and MAE 5.6761 EF points. The structured tabular baseline (E3; leakage-filtered, 51 retained features) achieved AUC 0.9153, R2 0.4383, and MAE 6.6842. The multimodal fusion model (E5; concatenated vision + tabular, linear heads) achieved AUC 0.9618, R2 0.6615, and MAE 5.3935, outperforming both unimodal baselines.

In the 10,000-bootstrap rerun, E5 achieved AUC 0.9618 (95% CI 0.9402-0.9797), compared with E2b AUC 0.9581 (95% CI 0.9334-0.9784) and E3 AUC 0.9153 (95% CI 0.8734-0.9513). These findings indicate a consistent multimodal gain over tabular-only performance and a modest but reproducible gain over vision-only performance.

### Structured measurement audit (E3)

The structured export contained 669,378 rows and 186 unique measurement names. After leakage controls and coverage filtering, 51 tabular features were retained for model training. Specifically, 4 measurements were excluded by leakage rules (LVEF-related) and 23 were excluded for low coverage. Mean retained-feature missingness was high (0.43), supporting the interpretation that tabular signal is informative but incomplete relative to image-derived information.

### Integrity and reproducibility checks

Postrun audit reported zero missing required files and zero structural warnings. Subject-level split consistency checks showed no leakage violations. All headline metrics and cohort counts were frozen with checksum manifests in the evidence package for downstream review and manuscript assembly.

## Suggested Manuscript Tables

### Table 1. Cohort derivation summary

- Eligible studies: 4,530
- Study embeddings available: 4,696
- LVEF evaluation cohort: 2,833
- Held-out test studies: 426
- LVEF studies missing embeddings: 0

### Table 2. Primary test-set performance (E2b/E3/E5)

- E2b (vision): AUC 0.9581, R2 0.6127, MAE 5.6761
- E3 (tabular): AUC 0.9153, R2 0.4383, MAE 6.6842
- E5 (fusion): AUC 0.9618, R2 0.6615, MAE 5.3935

### Table 3. Tabular feature curation and leakage audit

- Raw measurement types: 186
- Excluded as LVEF leakage: 4
- Excluded low coverage: 23
- Retained features: 51
- Mean missingness: 0.43

## Suggested Manuscript Figures

### Figure 1. Primary performance comparison across E2b/E3/E5

Three aligned panels on the same test split:
- AUC (higher better)
- R2 (higher better)
- MAE in EF points (lower better)

Purpose: one-glance demonstration of multimodal lift.

### Figure 2. Test AUC with 95% bootstrap confidence intervals

Point estimates and CIs for:
- vision-only (E2b)
- tabular-only (E3)
- fusion (E5)

Purpose: uncertainty-aware comparison that supports claim robustness.

## Reporting Note

For manuscript consistency, report both:
- full eligible cohort size (n=4,530), and
- task-specific LVEF evaluation size (n=2,833; test n=426).

Do not conflate these counts in Methods/Results text.

## Reproducible Asset Generation Command

```bash
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
PY=.venv-echoprime/bin/python
$PY scripts/generate_fullscale_results_assets.py \
  --fullscale-root outputs/cloud_cohorts/fullscale_all \
  --output-dir outputs/cloud_cohorts/fullscale_all/reporting_assets \
  --e5-bootstrap-metrics outputs/cloud_cohorts/fullscale_all/eval_e5_fusion_boot10k/fusion_metrics.json
```
