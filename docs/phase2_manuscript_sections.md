# Phase 2 Manuscript Sections: Imaging-Only LVOT VTI and TAPSE Baselines

## Methods

### Data Sources and Cohort

This analysis used MIMIC-IV-ECHO as the source of echocardiography DICOMs and structured echocardiographic measurements. The imaging cohort was defined from the processed MIMIC-IV-ECHO DICOM subset available through the project pipeline, rather than the entire structured-measurement denominator. Structured report measurements were used as labels. LVOT VTI was specified as the primary measurement target, and TAPSE was specified as a secondary measurement target.

EchoPrime was used to derive frozen study-level echocardiography embeddings. The primary analysis used all-clips study-level EchoPrime embeddings. ECHOVIEW view classifications were used only for view-filtered sensitivity analyses and were not used to define the primary modeling denominator.

### Cohort Construction

Target-positive studies were linked to available study-level EchoPrime embeddings. The LVOT VTI all-clips analysis included 3,782 studies with both a structured LVOT VTI target and a study-level embedding. These were split into 2,654 training, 573 validation, and 555 held-out test studies. The TAPSE all-clips analysis included 1,131 studies with both a structured TAPSE target and a study-level embedding, split into 787 training, 184 validation, and 160 held-out test studies.

Splits were deterministic and subject-level, with no subject overlap between training, validation, and test partitions. The validation split was used for model selection. The held-out test split was used only for final evaluation.

### Model Development and Evaluation

Frozen EchoPrime study embeddings were used as predictors. For each target, Ridge regression models were fit on the training split. Features were standardized with a `StandardScaler` fit on the training data only; the fitted transformation was then applied to validation and test data. Ridge models used the numerically stable `svd` solver. Regularization strength was selected on the validation split only from the prespecified alpha grid 0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300, and 1000. A train-median null model was evaluated as the reference baseline.

The primary evaluation metric was mean absolute error (MAE). Secondary continuous metrics included root mean squared error, R2, Pearson correlation, Spearman correlation, and Bland-Altman bias and limits of agreement. Subject-level bootstrap resampling was used to estimate 95% confidence intervals for key test-set metrics.

Exploratory binary low-VTI summaries were derived from continuous LVOT VTI predictions at thresholds of LVOT VTI <18 cm and <20 cm. These binary analyses were thresholded summaries of continuous predictions and were not separately trained classifiers.

### Sensitivity Analyses

Hard-extreme target exclusion was evaluated as a robustness check for both LVOT VTI and TAPSE. This sensitivity analysis excluded only hard invalid or extreme target values according to prespecified target rules and did not remove borderline physiologic outliers.

LVOT VTI ECHOVIEW-filtered sensitivity analyses used view-filtered embeddings from ECHOVIEW-labeled clips. Prespecified view policies included A5C-or-other, other-only, and A5C-only at probability threshold 0.70, with an additional A5C-or-other threshold ladder at 0.80, 0.90, and 0.95. These analyses were interpreted as limited subset analyses rather than the primary denominator because ECHOVIEW covers a derived view-classification subset.

## Results

### Cohort and Split Summary

The primary LVOT VTI all-clips cohort included 3,782 target-positive studies with study-level EchoPrime embeddings. The deterministic subject-level split included 2,654 training studies, 573 validation studies, and 555 test studies. The TAPSE all-clips cohort included 1,131 target-positive studies with study-level EchoPrime embeddings, split into 787 training studies, 184 validation studies, and 160 test studies.

### Primary LVOT VTI Performance

In the held-out LVOT VTI test set of 555 studies, the null median baseline had MAE 4.54 cm. The Ridge model selected alpha 0.3 and achieved MAE 3.64 cm, RMSE 4.66 cm, and R2 0.372 (Figure 1; Table 1). The subject-level bootstrap 95% CI was 3.41 to 3.88 cm for Ridge MAE and 0.30 to 0.43 for Ridge R2.

Bland-Altman analysis showed prediction-minus-observed bias +0.28 cm, with limits of agreement from -8.85 to +9.41 cm. For exploratory low-VTI thresholds derived from the continuous predictions, AUROC was 0.846 for LVOT VTI <18 cm and 0.812 for LVOT VTI <20 cm (Supplementary Table S3).

### LVOT VTI Robustness and ECHOVIEW Sensitivity Analyses

After hard-extreme target exclusion, the LVOT VTI test set remained 555 studies. The Ridge model achieved MAE 3.62 cm and R2 0.376 (Supplementary Table S1).

ECHOVIEW-filtered LVOT VTI analyses used smaller test sets than the all-clips analysis (Supplementary Table S2). The A5C-or-other policy at threshold 0.70 included 65 test studies and achieved MAE 4.07 cm and R2 0.151. The other-only policy at threshold 0.70 included 65 test studies and achieved MAE 4.14 cm and R2 0.123. The A5C-only policy at threshold 0.70 had 22 test studies and was skipped because of insufficient training data. A5C-or-other thresholds of 0.80, 0.90, and 0.95 did not improve performance; R2 estimates were approximately 0.12 to 0.14, with bootstrap confidence intervals crossing zero.

### TAPSE Secondary Endpoint Performance

In the held-out TAPSE test set of 160 studies, the null median baseline had MAE 3.79 mm. The Ridge model selected alpha 1000 and achieved MAE 3.17 mm, RMSE 3.94 mm, and R2 0.284 (Supplementary Figure S1; Table 1). The subject-level bootstrap 95% CI was 2.80 to 3.56 mm for Ridge MAE and 0.13 to 0.40 for Ridge R2.

Bland-Altman analysis showed prediction-minus-observed bias +0.22 mm, with limits of agreement from -7.52 to +7.96 mm. The hard-extreme sensitivity produced the same test-set size and similar performance, with MAE 3.17 mm and R2 0.284 (Supplementary Table S1).

### Exploratory Binary Low-VTI Summaries

For LVOT VTI <18 cm, test-set prevalence was 0.191 and AUROC was 0.846. At the reported operating point, sensitivity was 0.358 and specificity was 0.960. For LVOT VTI <20 cm, prevalence was 0.332 and AUROC was 0.812, with sensitivity 0.478 and specificity 0.911 (Supplementary Table S3).

## Discussion

The primary LVOT VTI model showed a moderate imaging-only estimation signal using frozen EchoPrime study-level embeddings. Compared with the train-median null baseline, Ridge regression reduced MAE by approximately 0.90 cm and achieved positive test-set R2. The low-VTI ROC findings suggest potential utility for exploratory risk stratification, but these analyses were thresholded summaries of continuous predictions and were not separately optimized classifiers.

The error distribution places important constraints on interpretation. Bland-Altman limits of agreement remained wide, indicating that the model should not be presented as a replacement for clinical Doppler LVOT VTI measurement. The analysis predicts structured report measurements and does not constitute independently adjudicated manual measurement or direct extraction of LVOT VTI from spectral Doppler traces.

The hard-extreme robustness analysis produced results similar to the primary LVOT VTI analysis, indicating that the primary result was not materially altered by excluding hard-extreme target values. ECHOVIEW-filtered LVOT VTI analyses were smaller and had weaker performance than the all-clips analysis. These findings support treating ECHOVIEW-filtered analyses as sensitivity analyses rather than as a competing primary modeling denominator or evidence of superior view selection.

TAPSE showed a secondary imaging-only signal under the same modeling framework. However, the TAPSE test set was smaller than the LVOT VTI test set, and the selected alpha of 1000 indicates strong regularization. TAPSE should therefore be interpreted as a secondary endpoint requiring additional validation.

## Limitations

This was a retrospective single-dataset analysis from a MIMIC-IV-ECHO derived cohort. Labels were structured report measurements, without independent manual remeasurement or adjudication. Label noise and measurement heterogeneity may therefore affect the reported performance. LVOT VTI is Doppler-derived and may not be directly visible in all all-clips study embeddings. ECHOVIEW analyses used a limited derived view-classification subset rather than the full DICOM denominator. The TAPSE analysis had a smaller sample size and selected a strongly regularized model. Binary low-VTI analyses were exploratory thresholded summaries of continuous predictions. External validation is needed before clinical generalization.

## Figure Caption Text

### Figure 1. Primary LVOT VTI Imaging-Only Model Performance

Frozen EchoPrime study embeddings were used to predict structured LVOT VTI on a held-out subject-level test split. Ridge regression used train-fit feature standardization, the numerically stable `svd` solver, and validation-only alpha selection. Panel A shows observed versus predicted LVOT VTI with identity and calibration lines. Panel B shows Bland-Altman agreement, with bias and limits of agreement. Panel C compares null median versus Ridge test MAE, with 95% confidence intervals shown where available. Panel D shows exploratory ROC curves for LVOT VTI <18 cm and <20 cm derived from the continuous predictions; these were not separately trained classifiers. The wide limits of agreement support an imaging-only estimation and risk-stratification signal rather than replacement of clinical Doppler LVOT VTI measurement.

### Supplementary Figure S1. TAPSE Secondary Endpoint Imaging-Only Model Performance

Frozen EchoPrime study embeddings were used to predict structured TAPSE on the held-out subject-level test split using the same stable-v2 Ridge configuration. Panel A shows observed versus predicted TAPSE with identity and calibration lines. Panel B shows Bland-Altman agreement. Panel C compares null median versus Ridge test MAE. TAPSE was evaluated as a cautious secondary endpoint because of the smaller test set and strong regularization, and should not be interpreted as measurement-grade automation.
