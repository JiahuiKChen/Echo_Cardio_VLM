# Phase 2.4 Manuscript Draft: Stable-v2 Imaging-Only TAPSE and LVOT VTI Baselines

## Provenance And Verification Status

This draft summarizes stable-v2 imaging-only baseline results for LVOT VTI and TAPSE. Values were verified against SCC aggregate-only outputs on 2026-06-24 using `scripts/summarize_phase2_imaging_results.py`.

Internal provenance note: the aggregate-only review packet was generated under `/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2/review_packets/phase2_verified_aggregate_only`. The verifier read only aggregate metric files and did not read or copy patient-level prediction rows, raw embeddings, manifests, DICOM paths, or SCC logs. Prediction CSVs were detected in the restricted run directories and explicitly excluded.

The verified aggregate inventory contained 10 runs: 2 main all-clips analyses, 2 hard-extreme robustness analyses, and 6 LVOT VTI ECHOVIEW/view-filtered sensitivity analyses. The strict A5C-only ECHOVIEW sensitivity was skipped because of insufficient training data and should not be interpreted as a negative result.

## Results Overview

The stable-v2 imaging-only baselines use frozen EchoPrime study embeddings, deterministic subject-level train/validation/test splits, train-fit feature standardization, and Ridge regression with the numerically stable `svd` solver. Ridge alpha was selected on the validation split only. The stable-v2 LVOT VTI all-clips model is the primary manuscript-facing imaging-only baseline because it has the strongest denominator, zero Ridge warnings, and the clearest held-out signal.

The primary LVOT VTI model demonstrated moderate predictive performance on the held-out test set. Compared with a train-median null baseline, Ridge regression reduced test MAE from 4.54 cm to 3.64 cm and achieved test R2 of 0.372. Subject-level bootstrap confidence intervals supported reproducible improvement, with Ridge test MAE 3.41 to 3.88 cm and Ridge test R2 0.30 to 0.43.

This result supports an imaging-only LVOT VTI estimation signal, but it should not be described as measurement-grade automation. Bland-Altman limits remained wide, and fewer than half of predictions were within 3 cm. The defensible claim is moderate imaging-only LVOT VTI estimation and exploratory low-flow risk stratification signal, not replacement of clinical LVOT VTI measurement.

## Primary LVOT VTI All-Clips Result

The LVOT VTI all-clips stable-v2 analysis included 3,782 target-positive studies with study embeddings and a held-out test set of 555 studies. The selected Ridge alpha was 0.3. On the held-out test set, the Ridge model reduced MAE from 4.54 cm for the null median baseline to 3.64 cm, an absolute improvement of approximately 0.91 cm and a relative improvement of approximately 20%. Test R2 was 0.372, with Pearson and Spearman correlations approximately 0.61 and 0.63.

Despite the improvement over null, the error magnitude remains clinically important. The test-set Bland-Altman limits were approximately -8.85 to 9.41 cm, and the proportion within 3 cm was approximately 0.48. These findings suggest that all-clips EchoPrime embeddings encode information correlated with LVOT VTI, but not with enough precision to replace Doppler-derived measurement.

Performance was essentially unchanged after excluding hard-extreme target values. The hard-extreme sensitivity had test MAE 3.62 cm and test R2 0.376, supporting robustness to implausible structured-measurement outliers without making this sensitivity analysis co-primary.

## LVOT VTI ECHOVIEW Sensitivity Results

ECHOVIEW-filtered LVOT VTI analyses were run as limited subset sensitivities, not as competing primary analyses. This distinction matters because ECHOVIEW is a derived view-classification subset and not the full available MIMIC-IV-ECHO DICOM denominator. Low ECHOVIEW overlap should not be interpreted as absence of relevant views or Doppler clips in the full DICOM corpus.

The leading Doppler-sensitive ECHOVIEW policy, A5C-or-other at threshold 0.70, included a test set of 65 studies. It showed directionally positive but attenuated performance, with Ridge test MAE 4.07 cm and test R2 0.151. The other-only 0.70 sensitivity was similar, with Ridge test MAE 4.14 cm and test R2 0.123. Stricter A5C-or-other thresholds at 0.80, 0.90, and 0.95 did not improve performance; R2 remained approximately 0.12 to 0.14 and bootstrap confidence intervals crossed zero. The strict A5C-only 0.70 subset had only 22 test studies and was skipped because of insufficient training data.

These sensitivity analyses support the primary interpretation rather than overturning it: ECHOVIEW-filtered subsets are directionally compatible with an imaging-only LVOT VTI signal, but they are too small and too uncertain to define the primary modeling denominator or prove superior view selection.

## TAPSE Secondary Endpoint Result

TAPSE was evaluated as a cautious secondary endpoint. The all-clips stable-v2 TAPSE analysis included 1,131 target-positive studies with study embeddings and a held-out test set of 160 studies. The selected Ridge alpha was 1000, indicating a strongly regularized model.

On the held-out test set, Ridge regression reduced MAE from 3.79 mm for the null median baseline to 3.17 mm and achieved test R2 of 0.284. Subject-level bootstrap confidence intervals for Ridge MAE were 2.80 to 3.56 mm, and R2 confidence intervals were 0.13 to 0.40. The hard-extreme sensitivity was materially unchanged because no TAPSE hard extremes were excluded.

These results indicate a secondary imaging-only TAPSE signal, but the smaller test set, weaker performance, and heavy regularization argue for cautious reporting. TAPSE should be framed as a secondary or exploratory endpoint unless additional validation supports stronger physiological precision.

## Binary Low-VTI Exploratory Results

Exploratory binary low-VTI summaries were extracted from the stable-v2 LVOT VTI all-clips aggregate metrics. These binary metrics are derived from the continuous Ridge predictions and should not be interpreted as separately optimized classifiers.

For low LVOT VTI below 18 cm, the held-out prevalence was 0.191 and AUROC was 0.846. At the model threshold used in the aggregate output, sensitivity was 0.358 and specificity was 0.960. For low LVOT VTI below 20 cm, prevalence was 0.332 and AUROC was 0.812, with sensitivity 0.478 and specificity 0.911. These results support exploratory low-flow risk stratification signal, with the important caveat that sensitivity is modest at the current threshold.

## Table 1. Main Imaging-Only Results

Caption draft: Main imaging-only baseline performance using frozen EchoPrime study embeddings and deterministic subject-level train/validation/test splits. Ridge models used train-fit feature standardization, the `svd` solver, and validation-only alpha selection. Bootstrap confidence intervals are subject-level test-set intervals. MAE units are cm for LVOT VTI and mm for TAPSE. Hard-extreme exclusions are summarized only as robustness notes because they did not materially change performance.

| Target | Clinical unit | Test N | Null MAE | Ridge MAE | Ridge MAE 95% CI | Ridge R2 | Ridge R2 95% CI | Selected alpha | Interpretation | Robustness note |
|---|---:|---:|---:|---:|---|---:|---|---:|---|---|
| LVOT VTI | cm | 555 | 4.54 cm | 3.64 cm | 3.41 to 3.88 cm | 0.372 | 0.30 to 0.43 | 0.3 | Primary imaging-only baseline; moderate signal, not measurement-grade. | Hard-extreme exclusion materially unchanged: MAE 3.62 cm, R2 0.376, delta MAE -0.02 cm. |
| TAPSE | mm | 160 | 3.79 mm | 3.17 mm | 2.80 to 3.56 mm | 0.284 | 0.13 to 0.40 | 1000 | Cautious secondary signal; strongly regularized. | Hard-extreme exclusion materially unchanged: MAE 3.17 mm, R2 0.284, delta MAE 0.00 mm. |

## Supplementary Table S1. Hard-Extreme Robustness Analyses

Caption draft: Hard-extreme robustness analyses using the same stable-v2 modeling configuration as the main all-clips analyses. These runs are reported as reviewer-facing robustness checks, not as co-primary analyses.

| Target | Analysis | Test N | Ridge MAE | Ridge MAE 95% CI | Ridge R2 | Ridge R2 95% CI | Selected alpha | Interpretation |
|---|---|---:|---:|---|---:|---|---:|---|
| LVOT VTI | Exclude hard extremes | 555 | 3.62 cm | 3.37 to 3.87 cm | 0.376 | 0.30 to 0.44 | 0.3 | Robustness check; not driven by the single LVOT VTI hard extreme. |
| TAPSE | Exclude hard extremes | 160 | 3.17 mm | 2.82 to 3.53 mm | 0.284 | 0.13 to 0.40 | 1000 | Robustness check; no TAPSE hard extremes were removed. |

## Supplementary Table S2. LVOT VTI ECHOVIEW Sensitivity Analyses

Caption draft: ECHOVIEW-filtered LVOT VTI sensitivity analyses using view-filtered mean-pooled clip embeddings. These analyses are limited subset checks because ECHOVIEW labels cover a derived subset of MIMIC-IV-ECHO rather than the full DICOM denominator. Ridge alpha was selected on the validation split only. The strict A5C-only threshold 0.70 analysis was skipped because of insufficient training data and should not be interpreted as a negative result.

| View Policy | Threshold | Test N | Ridge MAE | Ridge R2 | Bootstrap CI Note | Interpretation |
|---|---:|---:|---:|---:|---|---|
| A5C-or-other | 0.70 | 65 | 4.07 cm | 0.151 | MAE 3.32 to 4.83 cm; R2 -0.06 to 0.32 | Directionally positive but smaller and weaker than all-clips. |
| Other-only | 0.70 | 65 | 4.14 cm | 0.123 | MAE 3.36 to 4.93 cm; R2 -0.12 to 0.30 | Directionally positive but smaller and weaker than all-clips. |
| A5C-only | 0.70 | 22 | Not estimated | Not estimated | Not available | Skipped for insufficient training data; do not interpret as negative. |
| A5C-or-other | 0.80 | 65 | 4.10 cm | 0.140 | MAE 3.34 to 4.87 cm; R2 -0.10 to 0.32 | Higher threshold did not improve performance. |
| A5C-or-other | 0.90 | 65 | 4.14 cm | 0.118 | MAE 3.36 to 5.01 cm; R2 -0.13 to 0.32 | Higher threshold did not improve performance. |
| A5C-or-other | 0.95 | 65 | 4.14 cm | 0.119 | MAE 3.38 to 4.96 cm; R2 -0.12 to 0.31 | Higher threshold did not improve performance. |

## Supplementary Table S3. Exploratory Binary Low-VTI Results

Caption draft: Exploratory binary low-flow summaries derived from continuous LVOT VTI predictions for the stable-v2 all-clips model. These are thresholded summaries of continuous model output, not separately optimized classifiers.

| Threshold | Test N | Prevalence | Predicted positive rate | AUROC | Average precision | Sensitivity | Specificity | PPV | NPV | F1 | TP | FP | TN | FN |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LVOT VTI <18 cm | 555 | 0.191 | 0.101 | 0.846 | 0.569 | 0.358 | 0.960 | 0.679 | 0.864 | 0.469 | 38 | 18 | 431 | 68 |
| LVOT VTI <20 cm | 555 | 0.332 | 0.218 | 0.812 | 0.669 | 0.478 | 0.911 | 0.727 | 0.779 | 0.577 | 88 | 33 | 338 | 96 |

## Reviewer-Facing Interpretation

The primary imaging-only LVOT VTI model demonstrated moderate predictive performance on the held-out test set. Compared with a train-median null baseline, Ridge regression on frozen EchoPrime study embeddings reduced MAE from 4.54 cm to 3.64 cm and achieved R2 of 0.372. Subject-level bootstrap confidence intervals supported a reproducible improvement over the null model. Performance was essentially unchanged after excluding the single hard-extreme label, supporting robustness to extreme target values.

The LVOT VTI result should be interpreted as evidence that frozen echocardiography embeddings contain clinically relevant information related to LVOT VTI. However, because LVOT VTI is a Doppler-derived measurement and the primary all-clips embedding may capture study-level correlates rather than direct spectral-trace measurement, this model should not be framed as directly measuring LVOT VTI from Doppler traces. The appropriate claim is moderate imaging-only estimation and exploratory low-flow risk stratification signal.

ECHOVIEW-filtered LVOT analyses were directionally positive but substantially smaller and weaker than the full all-clips model. The A5C-or-other 0.70 policy achieved positive but attenuated performance, whereas stricter thresholds did not improve results and had confidence intervals crossing zero. These findings support using ECHOVIEW analyses as subset sensitivity checks rather than the primary modeling denominator.

The TAPSE all-clips model showed a secondary signal, improving over the null baseline with test MAE 3.17 mm and R2 0.284. However, the smaller test set and selected alpha of 1000 indicate a strongly regularized model. TAPSE should therefore be reported as a secondary endpoint requiring cautious interpretation.

## Defensible Versus Non-Defensible Claims

Defensible claims:

- Frozen EchoPrime study embeddings contain a reproducible imaging-only signal for LVOT VTI in the fullscale all-clips denominator.
- The LVOT VTI result is robust to exclusion of the single hard-extreme structured target value.
- ECHOVIEW-filtered LVOT sensitivity analyses are directionally compatible with the primary result but limited by small subset size.
- TAPSE shows a weaker secondary imaging-only signal under the same stable-v2 modeling framework.
- Stable-v2 numerically stabilizes Ridge estimation, with zero recorded Ridge warnings in the reported runs.
- Binary low-VTI summaries support exploratory risk-stratification signal, but not a deployment-ready classifier.

Claims to avoid:

- Do not claim measurement-grade LVOT VTI automation.
- Do not claim replacement of clinical Doppler LVOT VTI measurement.
- Do not claim direct extraction of LVOT VTI from spectral Doppler traces.
- Do not claim clinical deployment readiness.
- Do not claim ECHOVIEW-filtered results prove superior view selection.
- Do not claim the A5C-only sensitivity is negative; it was underpowered or skipped.
- Do not claim TAPSE physiological precision beyond the observed held-out metrics.

## Limitations

This is a retrospective single-dataset analysis using structured report measurements as targets. The imaging-only embeddings predict report-derived measurements rather than independent manual remeasurement. LVOT VTI is a Doppler-derived value, and the all-clips study embedding may capture correlated study-level information rather than direct spectral-trace measurement. ECHOVIEW covers a limited derived subset of MIMIC-IV-ECHO and should not be treated as the full available DICOM denominator. The ECHOVIEW-filtered LVOT analyses have substantially smaller test sets and wider uncertainty, and strict A5C-only filtering was underpowered. TAPSE is a secondary endpoint with a smaller test set and strong regularization. Prediction errors and limits of agreement remain too wide for replacement of clinical measurement. Binary low-VTI summaries are exploratory thresholded summaries of a continuous model and have modest sensitivity at the reported operating points. External validation and leakage-safe comparison against clinical covariate or report-derived baselines are needed before stronger clinical claims.

## Recommended Manuscript Table Structure

Main results table:

- LVOT VTI all-clips stable-v2.
- TAPSE all-clips stable-v2.
- Compact robustness note for hard-extreme exclusion.

Supplementary Table S1:

- LVOT VTI hard-extreme exclusion.
- TAPSE hard-extreme exclusion.

Supplementary Table S2:

- LVOT VTI A5C-or-other threshold 0.70.
- LVOT VTI other-only threshold 0.70.
- LVOT VTI A5C-only threshold 0.70, marked underpowered or skipped.
- LVOT VTI A5C-or-other thresholds 0.80, 0.90, and 0.95.

Supplementary Table S3 or exploratory appendix:

- Binary low LVOT VTI below 18 cm and below 20 cm.

## Next Analyses Before Manuscript Freeze

- Create a final aggregate-only review packet with no prediction CSVs, restricted manifests, embeddings, or SCC logs.
- Decide whether binary low-VTI results belong in the main text or supplement.
- Decide whether TAPSE belongs in the main results or supplement.
- Confirm that no patient-level outputs enter the repository.
- Consider calibration and Bland-Altman figure generation from restricted outputs, exporting only aggregate or deidentified figures after review.
- Consider a final comparison against clinical covariate or simple report-derived baselines only if target-specific leakage exclusions are encoded and reviewed.
