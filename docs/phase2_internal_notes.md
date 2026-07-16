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
| Exploratory binary threshold analyses | Supplementary Table S3 | Thresholded binary summaries derived from continuous LVOT VTI and TAPSE predictions. |
| Leakage-safe non-image baselines | Supplementary Table S4 | Demographics-only, study/acquisition-metadata-only, and combined non-image Ridge baselines. |

## Claims To Avoid

- Do not claim measurement-grade LVOT VTI or TAPSE automation.
- Do not claim replacement of clinical Doppler LVOT VTI measurement.
- Do not claim direct extraction of LVOT VTI from spectral Doppler traces.
- Do not claim direct extraction of TAPSE from M-mode or tricuspid-annular motion clips.
- Do not describe Phase 2 as EchoPrime fine-tuning; EchoPrime was used as a frozen feature extractor.
- Do not imply that all DICOM objects were used; the all-clips embeddings came from successfully processed multiframe cine clips.
- Do not claim clinical deployment readiness.
- Do not claim ECHOVIEW-filtered results prove superior view selection.
- Do not claim the A5C-only ECHOVIEW sensitivity was negative; it was skipped as underpowered.
- Do not describe binary threshold summaries as separately optimized classifiers.
- Do not overstate TAPSE precision given the smaller test set and selected alpha of 1000.

## Provenance Positioning Notes

- Use "mean-pooled all-clips study-level EchoPrime embeddings" for the primary imaging input.
- Define "frozen" as fixed EchoPrime encoder weights and fixed generated embeddings during downstream Ridge training.
- The trained Phase 2 model component was Ridge regression, not EchoPrime.
- If reviewers ask about Doppler or M-mode, the aggregate retention audit supports saying that the pipeline processed multiframe clips but available metadata fields were insufficient to classify retained clips reliably as Doppler, M-mode, or 2D/cine. Absence of keyword matches is not evidence of absence.
- Keep `docs/phase2_embedding_provenance.md` aligned with any future Methods revision.

## Reviewer-Risk Notes

- Results should remain numerical and avoid clinical interpretation beyond the reported metrics.
- Canonical Phase 2 table sources are the Markdown/CSV files under `docs/tables/phase2/`; regenerate the optional DOCX packet after S3/S4 placement is final.
- Discussion should acknowledge the wide Bland-Altman limits when interpreting LVOT VTI performance.
- ECHOVIEW analyses are best framed as limited subset sensitivities because of their smaller denominator and uncertainty.
- TAPSE is positive but secondary, smaller, and strongly regularized.
- Binary threshold AUROC values are exploratory and should be paired with the reported operating-point sensitivity and specificity.
- TAPSE `<17 mm` has 36 positive test cases and can be included as an exploratory supplementary thresholded summary, not as a separately trained classifier.
- Demographics-only baselines used approximate age at echo and sex; race/ethnicity was not included. LVOT VTI demographics-only performance was modestly above null but below imaging-only Ridge, while TAPSE demographics-only performance was near null.
- The `n_clips`/`n_dicoms` baseline performed near null for both LVOT VTI and TAPSE; this supports saying the embedding result was not explained by simple acquisition-volume metadata.
- Future-work language should point to measurement-view localization, Doppler-specific LVOT VTI processing, TAPSE M-mode/RV-focused clip processing, and raw-DICOM or clip-level modeling as separate next steps.

## Reviewer Follow-Up Decision Logic

- Keep demographics-only, study/acquisition-metadata-only, and combined non-image baselines distinct because clip counts and extraction success are image-pipeline metadata.
- Broader clinical covariates beyond age and sex remain optional future work if reviewers ask for a stronger non-image comparator.
- Include TAPSE `<17 mm` as a supplementary exploratory binary result, with sensitivity/specificity and a note that it was derived from continuous predictions.
- Keep current Doppler/M-mode limitation language because aggregate metadata could not classify retention reliably.
