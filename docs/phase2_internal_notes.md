# Phase 2 Internal Manuscript Notes

Revision note: converted the prior internal synthesis into manuscript-style sections; moved interpretation from Results to Discussion; expanded Methods to define MIMIC-IV-ECHO, EchoPrime, ECHOVIEW, cohort construction, splits, model development, and sensitivity analyses. Phase 2.10 separated manuscript-facing text from internal cautions and created canonical table source files under `docs/tables/phase2/`.

## Figure and Table Callout Map

| Manuscript element | Recommended callout | Purpose |
|---|---|---|
| Primary LVOT VTI model performance | Figure 1 | Main visual summary of observed-versus-predicted LVOT VTI, Bland-Altman agreement, MAE comparison, and exploratory low-VTI ROC curves. |
| Main continuous performance metrics | Table 1 | Primary LVOT VTI and secondary TAPSE all-clips stable-v2 results. |
| TAPSE secondary endpoint | Supplementary Figure S1 | Visual summary of TAPSE observed-versus-predicted, Bland-Altman, and MAE comparison panels. |
| Hard-extreme robustness | Supplementary Table S1 | Robustness analyses after hard-extreme target exclusion. |
| ECHOVIEW view-filtered sensitivities | Supplementary Table S2 | Limited subset LVOT VTI sensitivity analyses using ECHOVIEW view-filtered embeddings. |
| Exploratory binary low-VTI analyses | Supplementary Table S3 | Thresholded binary summaries derived from continuous LVOT VTI predictions. |

## Claims To Avoid

- Do not claim measurement-grade LVOT VTI or TAPSE automation.
- Do not claim replacement of clinical Doppler LVOT VTI measurement.
- Do not claim direct extraction of LVOT VTI from spectral Doppler traces.
- Do not claim clinical deployment readiness.
- Do not claim ECHOVIEW-filtered results prove superior view selection.
- Do not claim the A5C-only ECHOVIEW sensitivity was negative; it was skipped as underpowered.
- Do not describe binary low-VTI summaries as separately optimized classifiers.
- Do not overstate TAPSE precision given the smaller test set and selected alpha of 1000.

## Reviewer-Risk Notes

- Results should remain numerical and avoid clinical interpretation beyond the reported metrics.
- Discussion should acknowledge the wide Bland-Altman limits when interpreting LVOT VTI performance.
- ECHOVIEW analyses are best framed as limited subset sensitivities because of their smaller denominator and uncertainty.
- TAPSE is positive but secondary, smaller, and strongly regularized.
- Binary low-VTI AUROC values are exploratory and should be paired with the reported operating-point sensitivity and specificity.
