# Title Placeholder

Frozen EchoPrime Study-Level Embeddings for Structured LVOT VTI and TAPSE Prediction in MIMIC-IV-ECHO

## Abstract

### Introduction

Quantitative transthoracic echocardiographic measurements are central to cardiovascular assessment, but structured measurements are not always available for downstream research or automated phenotyping. We evaluated whether frozen EchoPrime study-level echocardiography embeddings contain information associated with structured left ventricular outflow tract velocity-time integral (LVOT VTI) and tricuspid annular plane systolic excursion (TAPSE) report measurements.

### Methods

We used MIMIC-IV-ECHO studies with structured report labels and processed DICOM-derived embeddings. EchoPrime was used as a frozen feature extractor: clip-level 512-dimensional embeddings were mean-pooled into study-level all-clips embeddings, and EchoPrime weights were not updated. Downstream Ridge regression models were trained using deterministic subject-level train/validation/test splits, train-fit standardization, and validation-only alpha selection. LVOT VTI was the primary target and TAPSE was a secondary target. Comparators included a train-median null model and leakage-safe non-image baselines using age, sex, and acquisition metadata. Exploratory binary summaries were derived from continuous predictions, not separately trained classifiers.

### Results

For LVOT VTI, 3,782 target-plus-embedding studies were available, with 2,654 training, 573 validation, and 555 test studies. The Ridge model achieved test MAE 3.64 cm versus 4.54 cm for the null model, with RMSE 4.66 cm, R2 0.372, and MAE 95% CI 3.41-3.88 cm. Exploratory AUROC was 0.846 for LVOT VTI <18 cm and 0.812 for LVOT VTI <20 cm. For TAPSE, 1,131 target-plus-embedding studies were available, with 787 training, 184 validation, and 160 test studies. The Ridge model achieved test MAE 3.17 mm versus 3.79 mm for the null model, with RMSE 3.94 mm, R2 0.284, and MAE 95% CI 2.80-3.56 mm. Exploratory AUROC for TAPSE <17 mm was 0.789. Age/sex and acquisition-metadata baselines were weaker than imaging embeddings.

### Conclusions

Frozen EchoPrime study-level embeddings predicted structured LVOT VTI and TAPSE report measurements better than null and leakage-safe non-image baselines. These findings support representation-level structured measurement prediction experiments, not EchoPrime fine-tuning, direct Doppler/M-mode measurement automation, measurement replacement, or clinical deployment.

## Keywords Placeholder

echocardiography; MIMIC-IV-ECHO; EchoPrime; LVOT VTI; TAPSE; foundation model; Ridge regression; structured measurements

## Introduction

Quantitative transthoracic echocardiography provides standardized measurements that support cardiovascular assessment, reporting, and longitudinal comparison. The ACC/AHA/ASE key data elements define structured echocardiographic measurements, including velocity-time integral as the integral of a flow velocity curve and a value reported in centimeters [Douglas2019]. As cardiovascular artificial intelligence increasingly uses imaging, reports, and clinical data, careful distinction is needed between structured measurement prediction, direct measurement automation, and clinically deployed decision support [Elias2024].

LVOT VTI and TAPSE are clinically relevant but methodologically distinct echocardiographic targets. LVOT VTI is a Doppler-derived measure related to stroke volume and cardiac output physiology, and it has been discussed in acute-care contexts such as fluid responsiveness and risk assessment [Douglas2019; Parker2022]. Low LVOT VTI has also been associated with adverse outcomes in disease-specific cohorts, although published thresholds vary by context [Yuriditsky2020]. TAPSE is a widely used marker of right ventricular systolic function, with TAPSE <17 mm identified as a benchmark for abnormal RV systolic function in expert consensus guidance, while RV assessment remains multiparametric and anatomically complex [OGara2026; Konstam2018].

Recent echocardiography AI studies span direct video-based measurement or interpretation systems and broader representation-learning foundation models. EchoNet-Dynamic established video-based deep learning for cardiac function assessment [Ouyang2020]. EchoCLIP and EchoPrime extended echocardiography foundation modeling with image/video-report representation learning and multi-view, view-informed vision-language modeling [Christensen2024; Vukadinovic2026]. In parallel, multitask and measurement-automation systems such as PanEcho and EchoNet-Measurements have targeted direct interpretation or measurement estimation from echocardiographic data [Holste2025; Sahashi2025]. Formal validation studies emphasize that direct automated echo interpretation requires rigorous comparison with human measurement variability and clinical workflows [Tromp2022].

MIMIC-IV-ECHO provides a public linked resource for studying echocardiographic imaging and structured measurements. The dataset includes structured echocardiographic measurements and a DICOM subset linked to MIMIC-IV, whose modular structure supports linkage to clinical tables and demographics [Gow2026; Johnson2023]. However, the DICOM subset is smaller than the full structured-measurement denominator, and representation-learning studies using processed image subsets must clearly define the denominator, preprocessing, labels, and evaluation design. Prior echocardiography AI studies have shown direct measurement automation and broad foundation-model performance, but it remains unclear whether frozen study-level foundation-model embeddings encode quantitative structured measurements such as Doppler-derived LVOT VTI and TAPSE in a public, linked echocardiography resource.

In this study, we evaluated whether frozen EchoPrime study-level all-clips embeddings could predict structured report measurements of LVOT VTI and TAPSE using downstream Ridge regression. LVOT VTI was specified as the primary endpoint and TAPSE as a cautious secondary endpoint. The analysis was designed as structured measurement prediction from fixed embeddings, not EchoPrime fine-tuning, not direct LVOT Doppler spectral-trace measurement, and not direct TAPSE measurement from M-mode or tricuspid-annular motion clips.

## Methods

### Data Sources and Cohort

This analysis used MIMIC-IV-ECHO as the source of echocardiography DICOMs and structured echocardiographic measurements [Gow2026]. MIMIC-IV provided the parent clinical database and linkage context for demographic features [Johnson2023]. The imaging cohort was defined from the processed MIMIC-IV-ECHO DICOM subset available through the project pipeline, rather than the entire structured-measurement denominator. Structured report measurements were used as labels. LVOT VTI was the primary measurement target, and TAPSE was the secondary measurement target.

### Cohort Construction

Target-positive studies were linked to available study-level EchoPrime embeddings. The LVOT VTI all-clips analysis included 3,782 studies with both a structured LVOT VTI target and a study-level embedding. These were split into 2,654 training, 573 validation, and 555 held-out test studies. The TAPSE all-clips analysis included 1,131 studies with both a structured TAPSE target and a study-level embedding, split into 787 training, 184 validation, and 160 held-out test studies. Splits were deterministic and subject-level, with no subject overlap between training, validation, and test partitions. The validation split was used for model selection, and the held-out test split was used only for final evaluation.

### Embedding Provenance

EchoPrime was used as a frozen feature extractor. In this context, frozen means that the pretrained EchoPrime encoder weights and generated embedding matrices were fixed during downstream model training. EchoPrime weights were not updated, and the Phase 2 analyses did not fine-tune EchoPrime. The primary analysis used mean-pooled all-clips study-level EchoPrime embeddings, aggregated from successfully embedded clip-level representations. The original EchoPrime model provides the foundation-model context for the video-based encoder and 512-dimensional representation, while the project provenance defines the specific frozen inference and mean-pooling workflow used here [Vukadinovic2026].

### Model Development and Evaluation

Frozen EchoPrime study embeddings were used as predictors. For each target, Ridge regression models were fit on the training split. Features were standardized with a `StandardScaler` fit on the training data only; the fitted transformation was then applied to validation and test data. Ridge models used the numerically stable `svd` solver. Regularization strength was selected on the validation split only from the prespecified alpha grid 0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300, and 1000. A train-median null model was evaluated as the reference baseline.

The model was trained to predict structured report measurements from study-level imaging embeddings. It was not designed to directly extract LVOT VTI from spectral Doppler traces or TAPSE from M-mode or tricuspid-annular motion clips. The primary evaluation metric was mean absolute error (MAE). Secondary continuous metrics included root mean squared error (RMSE), R2, Pearson correlation, Spearman correlation, and Bland-Altman bias and limits of agreement. Subject-level bootstrap resampling was used to estimate 95% confidence intervals for key test-set metrics. Reporting followed transparent prediction-model and cardiovascular imaging AI evaluation principles for predictor definitions, model development, validation, and limitations [Moons2015; Sengupta2020; Kagiyama2026].

### Sensitivity Analyses

Hard-extreme target exclusion was evaluated as a robustness check for both LVOT VTI and TAPSE. LVOT VTI ECHOVIEW-filtered sensitivity analyses used view-filtered embeddings from ECHOVIEW-labeled clips. Prespecified view policies included A5C-or-other, other-only, and A5C-only at probability threshold 0.70, with an additional A5C-or-other threshold ladder at 0.80, 0.90, and 0.95. These analyses were interpreted as limited subset analyses rather than the primary denominator because ECHOVIEW covers a derived view-classification subset.

### Non-Image Baselines

Leakage-safe non-image baselines were evaluated using the same target-positive study denominators and deterministic subject-level splits as the imaging-only analyses. The demographics-only baseline used approximate age at echo and sex. Approximate age at echo was derived from MIMIC-IV `anchor_age` and `anchor_year` adjusted by echo study year; the aggregate run used `study_datetime` as the study-year source for all selected studies. Race/ethnicity was not included because a direct echo-study-to-admission linkage was not used. A separate study/acquisition-metadata baseline used only the number of DICOMs and successfully embedded clips per study (`n_dicoms` and `n_clips`). A combined non-image baseline used demographics plus study/acquisition metadata. These baselines did not use EchoPrime embeddings, report text, diagnoses, indications, other echocardiographic measurements, qualitative echo findings, post-echo variables, or variables derived from echocardiogram interpretation.

### Exploratory Binary Threshold Summaries

Exploratory binary threshold summaries were derived from continuous predictions. LVOT VTI thresholds were <18 cm and <20 cm. TAPSE was summarized at <17 mm, a benchmark for abnormal RV systolic function in expert consensus guidance [OGara2026]. These binary analyses were thresholded summaries of continuous predictions and were not separately trained classifiers.

## Results

### Cohort and Split Summary

The primary LVOT VTI all-clips cohort included 3,782 target-positive studies with study-level EchoPrime embeddings. The deterministic subject-level split included 2,654 training studies, 573 validation studies, and 555 test studies. The TAPSE all-clips cohort included 1,131 target-positive studies with study-level EchoPrime embeddings, split into 787 training studies, 184 validation studies, and 160 test studies.

### Primary LVOT VTI Performance

In the held-out LVOT VTI test set of 555 studies, the null median baseline had MAE 4.54 cm. The imaging Ridge model selected alpha 0.3 and achieved MAE 3.64 cm, RMSE 4.66 cm, and R2 0.372 (Figure 1; Table 1). The subject-level bootstrap 95% CI was 3.41-3.88 cm for Ridge MAE and 0.30-0.43 for Ridge R2. Bland-Altman analysis showed prediction-minus-observed bias +0.28 cm, with limits of agreement from -8.85 to +9.41 cm. Exploratory binary threshold summaries are reported in Supplementary Table S3.

### TAPSE Secondary Endpoint Performance

In the held-out TAPSE test set of 160 studies, the null median baseline had MAE 3.79 mm. The imaging Ridge model selected alpha 1000 and achieved MAE 3.17 mm, RMSE 3.94 mm, and R2 0.284 (Supplementary Figure S1; Table 1). The subject-level bootstrap 95% CI was 2.80-3.56 mm for Ridge MAE and 0.13-0.40 for Ridge R2. Bland-Altman analysis showed prediction-minus-observed bias +0.22 mm, with limits of agreement from -7.52 to +7.96 mm. Exploratory TAPSE <17 mm summaries are reported in Supplementary Table S3.

### Robustness and ECHOVIEW Sensitivity Analyses

After hard-extreme target exclusion, the LVOT VTI test set remained 555 studies, and the imaging Ridge model achieved MAE 3.62 cm and R2 0.376 (Supplementary Table S1). The TAPSE hard-extreme sensitivity produced the same test-set size and similar performance, with MAE 3.17 mm and R2 0.284 (Supplementary Table S1).

ECHOVIEW-filtered LVOT VTI analyses used smaller test sets than the all-clips analysis (Supplementary Table S2). The A5C-or-other policy at threshold 0.70 included 65 test studies and achieved MAE 4.07 cm and R2 0.151. The other-only policy at threshold 0.70 included 65 test studies and achieved MAE 4.14 cm and R2 0.123. The A5C-only policy at threshold 0.70 had 22 test studies and was skipped because of insufficient training data. A5C-or-other thresholds of 0.80, 0.90, and 0.95 did not improve performance; R2 estimates were approximately 0.12-0.14, with bootstrap confidence intervals crossing zero.

### Non-Image Baselines

Leakage-safe non-image baselines were evaluated using demographics, study/acquisition metadata, and their combination (Supplementary Table S4). For LVOT VTI, the demographics-only Ridge model using approximate age at echo and sex achieved test MAE 4.47 cm and R2 0.043, compared with 4.54 cm and R2 -0.005 for the null model. The study/acquisition-metadata Ridge model using only `n_clips` and `n_dicoms` achieved test MAE 4.58 cm and R2 0.008. The combined demographics-plus-study/acquisition-metadata Ridge model achieved test MAE 4.48 cm and R2 0.048.

For TAPSE, the demographics-only Ridge model achieved test MAE 3.80 mm and R2 -0.024, compared with 3.79 mm and approximately zero R2 for the null model. The study/acquisition-metadata Ridge model achieved test MAE 3.77 mm and R2 0.0004. The combined demographics-plus-study/acquisition-metadata Ridge model achieved test MAE 3.79 mm and R2 -0.018.

### Exploratory Binary Threshold Summaries

For LVOT VTI <18 cm, test-set prevalence was 0.191 and AUROC was 0.846. At the reported operating point, sensitivity was 0.358 and specificity was 0.960. For LVOT VTI <20 cm, prevalence was 0.332 and AUROC was 0.812, with sensitivity 0.478 and specificity 0.911 (Supplementary Table S3).

For TAPSE <17 mm, the held-out test set included 36 positive cases among 160 studies, corresponding to prevalence 0.225. The thresholded summary derived from continuous TAPSE predictions had AUROC 0.789 and average precision 0.638. At the operating point defined by predicted TAPSE <17 mm, sensitivity was 0.472 and specificity was 0.944 (Supplementary Table S3).

## Discussion

This study found that frozen EchoPrime study-level all-clips embeddings were associated with structured LVOT VTI and TAPSE report measurements in MIMIC-IV-ECHO. For the primary LVOT VTI endpoint, the imaging-embedding Ridge model improved test MAE from 4.54 cm for the train-median null model to 3.64 cm, with R2 0.372. For the secondary TAPSE endpoint, the model improved test MAE from 3.79 mm to 3.17 mm, with R2 0.284. Leakage-safe age/sex and acquisition-metadata baselines were weaker than the imaging-embedding model for both targets. The binary threshold summaries were exploratory analyses derived from continuous predictions.

These findings should be interpreted in relation to, but distinct from, prior echocardiography AI and foundation-model work. EchoNet-Dynamic, PanEcho, and EchoNet-Measurements illustrate video-based or multitask approaches to direct functional assessment, diagnostic interpretation, or measurement automation [Ouyang2020; Holste2025; Sahashi2025]. EchoPrime and EchoCLIP provide foundation-model context for echocardiographic representation learning [Christensen2024; Vukadinovic2026]. The present study did not train an end-to-end image model, update EchoPrime weights, or directly segment or measure echocardiographic structures; it used frozen study-level embeddings as predictors in downstream Ridge regression.

The LVOT VTI result suggests that all-clips study-level embeddings contain information associated with structured LVOT VTI report values. LVOT VTI is a standardized Doppler-derived measurement related to flow and stroke-volume assessment, and low VTI has been discussed in acute-care and disease-specific outcome contexts [Douglas2019; Parker2022; Yuriditsky2020]. The AUROCs for LVOT VTI <18 cm and <20 cm provide hypothesis-generating thresholded summaries, but the evidence matrix does not support presenting these exact cut-points as validated clinical classifiers in this cohort. The Bland-Altman limits of agreement remained wide, and VTI measurement itself has nontrivial precision limits; therefore, these findings should not be framed as replacement of clinical Doppler LVOT VTI measurement [Jozwiak2019].

The TAPSE findings provide a secondary signal in a smaller cohort. The selected alpha of 1000 indicates strong regularization, and the test set included 160 studies. TAPSE <17 mm is a recognized benchmark for abnormal RV systolic function, but RV assessment remains multiparametric and TAPSE alone is limited [OGara2026; Konstam2018]. The TAPSE <17 mm AUROC of 0.789 should therefore be interpreted as a supplemental thresholded summary of a continuous regression model, not as a validated abnormal RV function classifier.

The non-image baseline results reduce concern that the embedding-model performance was explained only by simple demographics or acquisition-volume metadata. For LVOT VTI, demographics-only and combined non-image models modestly improved over the train-median null model but remained below the imaging-embedding Ridge model. For TAPSE, demographics-only, study/acquisition-metadata-only, and combined non-image baselines performed near the null model. These baseline comparisons align with transparent model reporting principles, but they do not prove a causal imaging mechanism [Moons2015; Sengupta2020; Kagiyama2026].

Several limitations define the claim boundary. The labels were structured report measurements, not independent manual remeasurements or adjudicated core-lab measurements. This was a retrospective single-dataset analysis without external validation. The primary imaging inputs were all-clips, mean-pooled study-level embeddings, not measurement-view-localized Doppler or M-mode clips. Available DICOM metadata were insufficient to classify retained clips reliably as Doppler, M-mode, or 2D/cine, so absence of metadata keyword matches should not be interpreted as absence of those acquisition types. ECHOVIEW analyses were limited subset sensitivities. Race/ethnicity and broader clinical covariates were not included in the leakage-safe non-image baselines. The binary threshold analyses were exploratory and were not separately trained classifiers.

Future work should evaluate measurement-view localization and clip-level modeling. For LVOT VTI, this could include explicit identification or processing of Doppler spectral clips. For TAPSE, future models could focus on RV-relevant apical views, annular motion, or M-mode-relevant acquisitions. Selected-clip models should be compared with all-clips study-level embeddings to determine whether measurement-relevant localization improves precision. Raw-DICOM, clip-level, or pixel-level direct measurement automation would be a separate study requiring independent measurement adjudication and validation standards comparable to direct automated echo interpretation studies [Tromp2022; Sahashi2025]. Prospective or deployment-oriented work would require additional reporting and evaluation beyond this retrospective representation-probing analysis [Liu2020].

## Limitations

This study was limited by its retrospective single-dataset design, use of structured report labels without independent manual remeasurement, and absence of external validation. The processed MIMIC-IV-ECHO DICOM subset was not the full structured-measurement denominator. The all-clips study-level embedding approach did not localize LVOT VTI spectral Doppler traces or TAPSE M-mode/tricuspid-annular motion clips. Available metadata could not reliably determine Doppler/M-mode retention. ECHOVIEW analyses were limited subset sensitivities rather than a primary denominator. Non-image baselines did not include race/ethnicity or broader clinical covariates. Exploratory LVOT VTI and TAPSE threshold summaries should not be interpreted as separately trained or clinically validated classifiers.

## Conclusion

Frozen EchoPrime study-level all-clips embeddings predicted structured LVOT VTI and TAPSE report measurements better than null and leakage-safe non-image baselines in MIMIC-IV-ECHO. The findings support cautious use of frozen echocardiography embeddings for structured measurement prediction and hypothesis generation, while remaining distinct from EchoPrime fine-tuning, direct Doppler/M-mode measurement automation, measurement replacement, or clinical deployment.

## Figure Legends

### Figure 1. Primary LVOT VTI Imaging-Only Model Performance

Frozen EchoPrime study-level embeddings were used to predict structured LVOT VTI report measurements on a held-out subject-level test split. Ridge regression used train-fit feature standardization, the numerically stable `svd` solver, and validation-only alpha selection. Panel A shows observed versus predicted LVOT VTI with identity and calibration lines. Panel B shows Bland-Altman agreement, with bias and limits of agreement. Panel C compares null median versus Ridge test MAE, with 95% confidence intervals shown where available. Panel D shows exploratory ROC curves for LVOT VTI <18 cm and <20 cm derived from the continuous predictions; these were not separately trained classifiers.

### Supplementary Figure S1. TAPSE Secondary Endpoint Imaging-Only Model Performance

Frozen EchoPrime study-level embeddings were used to predict structured TAPSE report measurements on the held-out subject-level test split using the same stable-v2 Ridge configuration. Panel A shows observed versus predicted TAPSE with identity and calibration lines. Panel B shows Bland-Altman agreement, with bias and limits of agreement. Panel C compares null median versus Ridge test MAE. TAPSE was evaluated as a cautious secondary endpoint, and this figure should not be interpreted as measurement-grade TAPSE automation.

## Tables and Supplementary Material Callout List

- Figure 1: primary LVOT VTI model performance.
- Table 1: main continuous performance metrics for LVOT VTI and TAPSE.
- Supplementary Figure S1: TAPSE secondary endpoint performance.
- Supplementary Table S1: hard-extreme robustness analyses.
- Supplementary Table S2: ECHOVIEW view-filtered LVOT VTI sensitivity analyses.
- Supplementary Table S3: exploratory binary threshold summaries derived from continuous predictions.
- Supplementary Table S4: leakage-safe non-image baselines.

## References Placeholder or Citation Key List

Citation keys used in this draft: [Christensen2024], [Douglas2019], [Elias2024], [Gow2026], [Holste2025], [Johnson2023], [Jozwiak2019], [Kagiyama2026], [Konstam2018], [Liu2020], [Moons2015], [OGara2026], [Ouyang2020], [Parker2022], [Sahashi2025], [Sengupta2020], [Tromp2022], [Vukadinovic2026], [Yuriditsky2020].

Full reference formatting remains a pre-submission task. Citation support details are tracked in `docs/phase2_citation_audit.md` and the source evidence matrix.

## Citation Audit Appendix

The full citation audit is maintained in `docs/phase2_citation_audit.md`. In brief:

- Clinical measurement definitions and target context are supported by [Douglas2019], [Parker2022], [Yuriditsky2020], [OGara2026], [Konstam2018], and [Jozwiak2019].
- Dataset context is supported by [Gow2026] and [Johnson2023].
- Echo AI and foundation-model context is supported by [Ouyang2020], [Christensen2024], [Vukadinovic2026], [Holste2025], [Sahashi2025], and [Tromp2022].
- Reporting and validation framing is supported by [Moons2015], [Sengupta2020], [Kagiyama2026], and [Liu2020].
- Project-specific claims about frozen inference, mean pooling, Ridge regression, and verified metrics are supported by project provenance, scripts, and aggregate tables rather than external literature.

## Pre-Submission Checklist

- Final references need journal-specific formatting and verification.
- Journal word limits and structured abstract requirements need confirmation.
- DOCX table packet should be refreshed from canonical Markdown/CSV if any table source changes.
- Final figures need journal-format export and coauthor visual review.
- Coauthor review is required for Methods, Results, Discussion, and figure/table interpretation.
- Claim-boundary review is required before submission.
- External validation limitation must remain unless new validation is performed.
