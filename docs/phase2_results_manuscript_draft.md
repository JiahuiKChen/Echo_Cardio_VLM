# Phase 2.3 Manuscript Draft: Stable-v2 Imaging-Only TAPSE and LVOT VTI Baselines

## Provenance And Verification Status

This draft summarizes Phase 2.2 stable-v2 imaging-only baseline results for LVOT VTI and TAPSE. The local desktop environment used to create this document did not have the SCC output root mounted:

`/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2`

Therefore, the numbers below are based on aggregate SCC summaries pasted into the project thread, not on direct re-extraction from local aggregate CSV/JSON files. Patient-level prediction files were not inspected, copied, or committed. Before manuscript freeze, exact values should be regenerated from the SCC aggregate-only review packet and checked against this draft.

## Results Overview

The stable-v2 imaging-only baselines use frozen EchoPrime study embeddings, deterministic subject-level train/validation/test splits, train-fit feature standardization, and Ridge regression with the numerically stable `svd` solver. Ridge alpha was selected on the validation split only. The stable-v2 LVOT VTI all-clips model is the primary manuscript-facing imaging-only baseline because it has the strongest denominator, no numerical warnings, and the clearest held-out signal.

The primary LVOT VTI model demonstrated moderate predictive performance on the held-out test set. Compared with a train-median null baseline, Ridge regression reduced test MAE from 4.54 cm to 3.64 cm and achieved test R2 of 0.372. Subject-level bootstrap confidence intervals supported reproducible improvement, with Ridge test MAE approximately 3.41 to 3.88 cm and Ridge test R2 approximately 0.30 to 0.43.

This result supports an imaging-only LVOT VTI estimation signal, but it should not be described as measurement-grade automation. Bland-Altman limits remained wide, and only about half of predictions were within 3 cm. The defensible claim is moderate imaging-only LVOT VTI estimation and low-flow risk-stratification signal, not replacement of clinical LVOT VTI measurement.

## Primary LVOT VTI All-Clips Result

The LVOT VTI all-clips stable-v2 analysis included 3,782 target-positive studies with study embeddings and a held-out test set of 555 studies. The selected Ridge alpha was 0.3. On the held-out test set, the Ridge model reduced MAE from 4.54 cm for the null median baseline to 3.64 cm, an absolute improvement of approximately 0.90 cm and a relative improvement of approximately 20%. Test R2 was 0.372, with Pearson and Spearman correlations approximately 0.61 and 0.63.

Despite the improvement over null, the error magnitude remains clinically important. The test-set Bland-Altman limits were approximately -8.85 to 9.41 cm, and the proportion within 3 cm was approximately 0.48. These findings suggest that the all-clips EchoPrime embeddings encode information correlated with LVOT VTI, but not with enough precision to replace Doppler-derived measurement.

## LVOT VTI Hard-Extreme Robustness Result

The hard-extreme sensitivity excluded the single hard invalid or extreme LVOT VTI value identified during Phase 1 auditing. The analysis retained 3,781 target-positive studies with study embeddings and the same held-out test count of 555 studies. Performance was essentially unchanged: Ridge test MAE was 3.62 cm and test R2 was 0.376, with zero Ridge warnings and the same selected alpha of 0.3.

This supports the robustness of the LVOT VTI signal. The primary result is not driven by the single hard-extreme structured measurement value.

## LVOT VTI ECHOVIEW Sensitivity Results

ECHOVIEW-filtered LVOT VTI analyses were run as limited subset sensitivities, not as competing primary analyses. This distinction matters because ECHOVIEW is a derived view-classification subset and not the full available MIMIC-IV-ECHO DICOM denominator. Low ECHOVIEW overlap should not be interpreted as absence of relevant views or Doppler clips in the full DICOM corpus.

The leading Doppler-sensitive ECHOVIEW policy, A5C-or-other at threshold 0.70, included a test set of 65 studies. It showed directionally positive but attenuated performance, with Ridge test MAE 4.07 cm and test R2 0.151. The other-only 0.70 sensitivity was similar, with Ridge test MAE 4.13 cm and test R2 0.123. Stricter A5C-or-other thresholds at 0.80, 0.90, and 0.95 did not improve performance; R2 remained approximately 0.10 to 0.15, with bootstrap confidence intervals crossing zero. The strict A5C-only 0.70 subset had only 22 test studies and was skipped or considered underpowered.

These sensitivity analyses support the primary interpretation rather than overturning it: ECHOVIEW-filtered subsets are directionally compatible with an imaging-only LVOT VTI signal, but they are too small and too uncertain to define the primary modeling denominator or prove superior view selection.

## TAPSE Secondary Endpoint Result

TAPSE was evaluated as a cautious secondary endpoint. The all-clips stable-v2 TAPSE analysis included 1,131 target-positive studies with study embeddings and a held-out test set of 160 studies. The selected Ridge alpha was 1000, indicating a strongly regularized model.

On the held-out test set, Ridge regression reduced MAE from approximately 3.79 mm for the null median baseline to 3.17 mm and achieved test R2 of 0.284. Subject-level bootstrap confidence intervals for Ridge MAE were approximately 2.80 to 3.56 mm, and R2 confidence intervals were approximately 0.13 to 0.40. The hard-extreme sensitivity was unchanged because no TAPSE hard extremes were excluded.

These results indicate a secondary imaging-only TAPSE signal, but the smaller test set, weaker performance, and heavy regularization argue for cautious reporting. TAPSE should be framed as a secondary or exploratory endpoint unless additional validation supports stronger physiological precision.

## Binary Low-VTI Exploratory Results

The stable-v2 aggregate binary metrics file was not locally available when this draft was created. Binary low-VTI results should not be frozen for manuscript text until exact values are extracted from `imaging_baseline_binary_metrics.csv` for the stable-v2 all-clips and key sensitivity runs.

If included, the binary table should report low LVOT VTI thresholds below 18 cm and below 20 cm, with prevalence, sensitivity, specificity, PPV, NPV, F1, AUROC, and average precision. These analyses should be described as exploratory risk-stratification summaries of the continuous model output, not as separately optimized classifiers.

## Table 1. Primary And Key Robustness Imaging-Only Results

Caption draft: Imaging-only baseline performance using frozen EchoPrime study embeddings and deterministic subject-level train/validation/test splits. Ridge models used train-fit feature standardization, the `svd` solver, and validation-only alpha selection. Bootstrap confidence intervals are subject-level test-set intervals where available. MAE units are cm for LVOT VTI and mm for TAPSE.

| Target | Analysis | Test N | Null MAE | Ridge MAE | Ridge MAE 95% CI | Ridge R2 | Ridge R2 95% CI | Selected alpha | Interpretation |
|---|---:|---:|---:|---:|---|---:|---|---:|---|
| LVOT VTI | All-clips stable-v2 | 555 | 4.54 cm | 3.64 cm | 3.41 to 3.88 cm | 0.372 | 0.30 to 0.43 | 0.3 | Primary imaging-only baseline; moderate signal, not measurement-grade. |
| LVOT VTI | Exclude hard extreme | 555 | 4.54 cm | 3.62 cm | 3.37 to 3.87 cm | 0.376 | 0.30 to 0.44 | 0.3 | Essentially unchanged; not driven by single hard extreme. |
| TAPSE | All-clips stable-v2 | 160 | 3.79 mm | 3.17 mm | 2.80 to 3.56 mm | 0.284 | 0.13 to 0.40 | 1000 | Cautious secondary signal; strongly regularized. |
| TAPSE | Exclude hard extremes | 160 | 3.79 mm | 3.17 mm | 2.82 to 3.53 mm | 0.276 | 0.13 to 0.40 | 1000 | No TAPSE hard extremes excluded; confirms stability. |

## Table 2. LVOT VTI ECHOVIEW Sensitivity Analyses

Caption draft: ECHOVIEW-filtered LVOT VTI sensitivity analyses using view-filtered mean-pooled clip embeddings. These analyses are limited subset checks because ECHOVIEW labels cover a derived subset of MIMIC-IV-ECHO rather than the full DICOM denominator. Ridge alpha was selected on the validation split only. The strict A5C-only threshold 0.70 analysis was underpowered and should not be interpreted as a negative result.

| View Policy | Threshold | Test N | Ridge MAE | Ridge R2 | Bootstrap CI Note | Interpretation |
|---|---:|---:|---:|---:|---|---|
| A5C-or-other | 0.70 | 65 | 4.07 cm | 0.151 | MAE 3.32 to 4.83 cm; R2 -0.06 to 0.32 | Directionally positive but weaker and uncertain. |
| Other-only | 0.70 | 65 | 4.13 cm | 0.123 | MAE 3.36 to 4.93 cm; R2 -0.12 to 0.30 | Similar to A5C-or-other; consistent with Doppler-sensitive subset limitation. |
| A5C-only | 0.70 | 22 | Not estimated | Not estimated | Skipped or empty outputs due to small split counts | Underpowered; do not interpret as negative. |
| A5C-or-other | 0.80 | 65 | 4.10 cm | 0.140 | MAE 3.34 to 4.87 cm; R2 -0.10 to 0.32 | Higher threshold did not improve performance. |
| A5C-or-other | 0.90 | 65 | 4.14 cm | 0.118 | MAE 3.36 to 5.01 cm; R2 -0.13 to 0.32 | Higher threshold did not improve performance. |
| A5C-or-other | 0.95 | 65 | 4.14 cm | 0.119 | MAE 3.38 to 4.96 cm; R2 -0.12 to 0.31 | Higher threshold did not improve performance. |

## Table 3. Optional Binary Low-VTI Exploratory Results

Caption draft: Exploratory binary low-flow summaries derived from continuous LVOT VTI predictions. These should be reported only after exact stable-v2 aggregate binary metrics are extracted from SCC outputs. Thresholds should include low LVOT VTI below 18 cm and below 20 cm. These are not separately optimized classifiers.

| Target | Analysis | Threshold | Test N | Prevalence | AUROC | Average precision | Sensitivity | Specificity | PPV | NPV | Interpretation |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| LVOT VTI | All-clips stable-v2 | <18 cm | Pending exact aggregate extraction | Pending | Pending | Pending | Pending | Pending | Pending | Pending | Exploratory risk-stratification summary. |
| LVOT VTI | All-clips stable-v2 | <20 cm | Pending exact aggregate extraction | Pending | Pending | Pending | Pending | Pending | Pending | Pending | Exploratory risk-stratification summary. |

## Reviewer-Facing Interpretation

The primary imaging-only LVOT VTI model demonstrated moderate predictive performance on the held-out test set. Compared with a train-median null baseline, Ridge regression on frozen EchoPrime study embeddings reduced MAE from approximately 4.54 cm to 3.64 cm and achieved R2 of approximately 0.37. Subject-level bootstrap confidence intervals supported a reproducible improvement over the null model. Performance was essentially unchanged after excluding the single hard-extreme label, supporting robustness to extreme target values.

The LVOT VTI result should be interpreted as evidence that frozen echocardiography embeddings contain clinically relevant information related to LVOT VTI. However, because LVOT VTI is a Doppler-derived measurement and the primary all-clips embedding may capture study-level correlates rather than direct spectral trace measurement, this model should not be framed as directly measuring LVOT VTI from Doppler traces. The appropriate claim is moderate imaging-only estimation and possible low-flow risk stratification signal.

ECHOVIEW-filtered LVOT analyses were directionally positive but substantially smaller and weaker than the full all-clips model. The A5C-or-other 0.70 policy achieved positive but attenuated performance, whereas stricter thresholds did not improve results and had confidence intervals crossing zero. These findings support using ECHOVIEW analyses as subset sensitivity checks rather than the primary modeling denominator.

The TAPSE all-clips model showed a secondary signal, improving over the null baseline with test MAE approximately 3.17 mm and R2 approximately 0.28. However, the smaller test set and selected alpha of 1000 indicate a strongly regularized model. TAPSE should therefore be reported as a secondary endpoint requiring cautious interpretation.

## Defensible Versus Non-Defensible Claims

Defensible claims:

- Frozen EchoPrime study embeddings contain a reproducible imaging-only signal for LVOT VTI in the fullscale all-clips denominator.
- The LVOT VTI result is robust to exclusion of the single hard-extreme structured target value.
- ECHOVIEW-filtered LVOT sensitivity analyses are directionally compatible with the primary result but limited by small subset size.
- TAPSE shows a weaker secondary imaging-only signal under the same stable-v2 modeling framework.
- Stable-v2 numerically stabilizes Ridge estimation, with zero recorded Ridge warnings in the reported runs.

Claims to avoid:

- Do not claim measurement-grade LVOT VTI automation.
- Do not claim replacement of clinical Doppler LVOT VTI measurement.
- Do not claim direct extraction of LVOT VTI from spectral Doppler traces.
- Do not claim clinical deployment readiness.
- Do not claim ECHOVIEW-filtered results prove superior view selection.
- Do not claim the A5C-only sensitivity is negative; it was underpowered or skipped.
- Do not claim TAPSE physiological precision beyond the observed held-out metrics.

## Limitations

This is a retrospective single-dataset analysis using structured report measurements as targets. The imaging-only embeddings predict report-derived measurements rather than independent manual remeasurement. LVOT VTI is a Doppler-derived value, and the all-clips study embedding may capture correlated study-level information rather than direct spectral-trace measurement. ECHOVIEW covers a limited derived subset of MIMIC-IV-ECHO and should not be treated as the full available DICOM denominator. The ECHOVIEW-filtered LVOT analyses have substantially smaller test sets and wider uncertainty, and strict A5C-only filtering was underpowered. TAPSE is a secondary endpoint with a smaller test set and strong regularization. Prediction errors and limits of agreement remain too wide for replacement of clinical measurement. External validation and leakage-safe comparison against clinical covariate or report-derived baselines are needed before stronger clinical claims.

## Recommended Manuscript Table Structure

Main results table:

- LVOT VTI all-clips stable-v2.
- LVOT VTI hard-extreme exclusion.
- TAPSE all-clips stable-v2.
- TAPSE hard-extreme exclusion.

Supplementary sensitivity table:

- LVOT VTI A5C-or-other threshold 0.70.
- LVOT VTI other-only threshold 0.70.
- LVOT VTI A5C-only threshold 0.70, marked underpowered or skipped.
- LVOT VTI A5C-or-other thresholds 0.80, 0.90, and 0.95.

Optional exploratory table:

- Binary low LVOT VTI below 18 cm and below 20 cm, included only after exact stable-v2 aggregate extraction.

## Next Analyses Before Manuscript Freeze

- Verify exact aggregate values from all stable-v2 output files on SCC.
- Create a final aggregate-only review packet with no prediction CSVs, restricted manifests, embeddings, or SCC logs.
- Decide whether binary low-VTI results belong in the main text or supplement.
- Decide whether TAPSE belongs in the main results or supplement.
- Confirm that no patient-level outputs enter the repository.
- Consider calibration and Bland-Altman figure generation from restricted outputs, exporting only aggregate or deidentified figures after review.
- Consider a final comparison against clinical covariate or simple report-derived baselines only if target-specific leakage exclusions are encoded and reviewed.
