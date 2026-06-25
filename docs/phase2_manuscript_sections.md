# Phase 2 Manuscript Sections: Imaging-Only LVOT VTI and TAPSE Baselines

## Methods Draft: Imaging-Only Modeling

We evaluated whether frozen echocardiography embeddings contained signal for structured LVOT VTI and TAPSE measurements. For each study, we used precomputed EchoPrime study-level embeddings as fixed imaging-only features. Structured measurement targets were extracted from the echocardiography measurement table, using LVOT VTI as the primary target and TAPSE as a cautious secondary target. Analyses used deterministic subject-level train, validation, and test splits to prevent the same patient from contributing studies across modeling partitions.

For each target, we fit Ridge regression models on the training split. Features were standardized using parameters fit on the training data only, and the same transformation was then applied to validation and test splits. Ridge models used the numerically stable `svd` solver. Regularization strength was selected exclusively on the validation split from a prespecified alpha grid; the held-out test split was used only once for final performance reporting. A train-median null model was evaluated as the reference baseline.

Continuous performance was summarized on the held-out test split using mean absolute error (MAE), root mean squared error, R2, Pearson and Spearman correlation, calibration summaries, and Bland-Altman bias and limits of agreement. Subject-level bootstrap resampling was used to estimate 95% confidence intervals for key test-set metrics. Hard-extreme target exclusion was evaluated as a robustness sensitivity to assess whether performance was driven by implausible structured-measurement outliers; borderline physiologic outliers were not silently removed.

ECHOVIEW-filtered LVOT VTI analyses were performed as limited subset sensitivities. View-filtered embeddings were derived from ECHOVIEW-labeled clips under prespecified view policies, including A5C-or-other and other-only at probability threshold 0.70, with an A5C-or-other threshold ladder at 0.80, 0.90, and 0.95. These analyses were treated as subset sensitivity checks rather than competing primary denominators because ECHOVIEW labels cover a derived subset rather than the full available DICOM corpus.

Exploratory binary low-VTI summaries were derived from the continuous LVOT VTI predictions using thresholds of LVOT VTI <18 cm and <20 cm. These binary metrics were thresholded summaries of the continuous Ridge predictions and were not separately optimized classifiers.

## Results Draft

### Primary LVOT VTI Imaging-Only Model

The primary all-clips LVOT VTI model showed moderate imaging-only predictive performance on the held-out subject-level test set (Figure 1; Table 1). Among 555 test studies, Ridge regression reduced MAE from 4.54 cm for the train-median null baseline to 3.64 cm. Test-set R2 was 0.372. Subject-level bootstrap confidence intervals supported a reproducible signal, with Ridge MAE 95% CI 3.41 to 3.88 cm and Ridge R2 95% CI 0.30 to 0.43.

Bland-Altman analysis showed a mean prediction-minus-observed bias of +0.28 cm, with limits of agreement from -8.85 to +9.41 cm. Thus, although frozen EchoPrime study embeddings encoded information related to LVOT VTI, the remaining error was too large for measurement-grade automation. These findings support an imaging-only estimation and risk-stratification signal rather than replacement of clinical Doppler LVOT VTI measurement.

Exploratory low-VTI summaries derived from the continuous LVOT VTI predictions showed AUROC 0.846 for LVOT VTI <18 cm and AUROC 0.812 for LVOT VTI <20 cm (Supplementary Table S3). These binary summaries were not separately trained classifiers and should be interpreted as exploratory thresholded summaries of the continuous model output.

### Robustness and ECHOVIEW Sensitivity Analyses

The LVOT VTI result was not driven by the single hard-extreme structured target value. After excluding hard-extreme targets, the held-out test MAE was 3.62 cm and test R2 was 0.376, essentially unchanged from the primary all-clips model (Supplementary Table S1).

ECHOVIEW-filtered LVOT VTI analyses were directionally positive but weaker and substantially smaller than the primary all-clips analysis (Supplementary Table S2). The A5C-or-other policy at threshold 0.70 included 65 test studies and achieved MAE 4.07 cm with R2 0.151. The other-only policy at threshold 0.70 also included 65 test studies and achieved MAE 4.14 cm with R2 0.123. The strict A5C-only 0.70 analysis was skipped as underpowered because of insufficient training data, and should not be interpreted as a negative result. Stricter A5C-or-other thresholds at 0.80, 0.90, and 0.95 did not improve performance, and bootstrap confidence intervals for R2 crossed zero. These subset sensitivities support the primary interpretation but do not establish superior view selection or define a preferred primary modeling denominator.

### TAPSE Secondary Endpoint

TAPSE was evaluated as a cautious secondary endpoint using the same stable-v2 modeling framework (Supplementary Figure S1; Table 1). Among 160 held-out test studies, Ridge regression reduced MAE from 3.79 mm for the train-median null baseline to 3.17 mm. Test-set R2 was 0.284. Subject-level bootstrap confidence intervals were 2.80 to 3.56 mm for Ridge MAE and 0.13 to 0.40 for Ridge R2.

Bland-Altman analysis showed prediction-minus-observed bias of +0.22 mm, with limits of agreement from -7.52 to +7.96 mm. The selected Ridge alpha was 1000, indicating a strongly regularized model. Together with the smaller test set, this supports reporting TAPSE as a secondary imaging-only signal rather than as a measurement-grade TAPSE automation result.

## Limitations Draft

This analysis was retrospective and used a single dataset. The modeling targets were structured report measurements rather than independent manual remeasurement, so model performance reflects prediction of report-derived measurements rather than a prospective measurement workflow. LVOT VTI is a Doppler-derived value; all-clips study embeddings may capture correlated study-level information rather than direct spectral-trace measurement. Prediction error and Bland-Altman limits of agreement remained too wide for replacement of clinical LVOT VTI or TAPSE measurement.

ECHOVIEW-filtered analyses were limited by the derived ECHOVIEW-labeled subset and should not be treated as the full available DICOM denominator. The ECHOVIEW LVOT VTI test sets were substantially smaller than the all-clips test set, and the strict A5C-only sensitivity was underpowered. TAPSE was evaluated in a smaller test set and selected a high regularization strength, supporting cautious secondary interpretation. Binary low-VTI summaries were exploratory thresholded summaries of continuous predictions and had modest sensitivity at the reported operating points. External validation, prospective evaluation, and leakage-safe comparison against clinical covariate baselines are needed before stronger clinical claims.

## Figure and Table Callout Map

| Manuscript element | Recommended callout | Purpose |
|---|---|---|
| Primary LVOT VTI performance | Figure 1 | Main visual summary of observed-versus-predicted LVOT VTI, Bland-Altman agreement, MAE comparison, and exploratory low-VTI ROC curves. |
| Main continuous metrics | Table 1 | Primary LVOT VTI and secondary TAPSE all-clips stable-v2 results. |
| TAPSE secondary endpoint | Supplementary Figure S1 | Visual summary of TAPSE observed-versus-predicted, Bland-Altman, and MAE comparison panels. |
| Hard-extreme robustness | Supplementary Table S1 | Demonstrates LVOT VTI and TAPSE results are materially unchanged after hard-extreme exclusion. |
| LVOT ECHOVIEW sensitivities | Supplementary Table S2 | Reports limited subset sensitivity analyses and underpowered A5C-only run. |
| Binary low-VTI summaries | Supplementary Table S3 | Exploratory low-VTI threshold summaries derived from continuous LVOT VTI predictions. |

## Figure Caption Text

### Figure 1. Primary LVOT VTI Imaging-Only Model Performance

Frozen EchoPrime study embeddings were used to predict structured LVOT VTI on a held-out subject-level test split. Ridge regression used train-fit feature standardization, the numerically stable `svd` solver, and validation-only alpha selection. Panel A shows observed versus predicted LVOT VTI with identity and calibration lines. Panel B shows Bland-Altman agreement, with bias and limits of agreement. Panel C compares null median versus Ridge test MAE, with 95% confidence intervals shown where available. Panel D shows exploratory ROC curves for LVOT VTI <18 cm and <20 cm derived from the continuous predictions; these were not separately trained classifiers. The wide limits of agreement support an imaging-only estimation and risk-stratification signal rather than replacement of clinical Doppler LVOT VTI measurement.

### Supplementary Figure S1. TAPSE Secondary Endpoint Imaging-Only Model Performance

Frozen EchoPrime study embeddings were used to predict structured TAPSE on the held-out subject-level test split using the same stable-v2 Ridge configuration. Panel A shows observed versus predicted TAPSE with identity and calibration lines. Panel B shows Bland-Altman agreement. Panel C compares null median versus Ridge test MAE. TAPSE was evaluated as a cautious secondary endpoint because of the smaller test set and strong regularization, and should not be interpreted as measurement-grade automation.

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

- The strongest reviewer-facing claim is that frozen imaging embeddings contain reproducible LVOT VTI-related signal, not that the model performs clinical measurement.
- The Bland-Altman limits are wide and should be acknowledged wherever LVOT VTI performance is discussed.
- The ECHOVIEW subset is useful as a sensitivity analysis, but its smaller denominator and crossing R2 confidence intervals make it unsuitable as the primary analysis.
- The TAPSE signal is positive but weaker than LVOT VTI and strongly regularized; it is best reported as secondary or supplementary depending on manuscript space.
- Binary low-VTI AUROC values are encouraging, but threshold sensitivity is modest and should be framed as exploratory risk stratification.
