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
| Exploratory TAPSE <17 mm analysis | Supplementary Table S3 or S4 | Thresholded binary summary derived from continuous TAPSE predictions if aggregate counts are adequate. |
| Leakage-safe non-image baselines | Supplementary table pending | Demographics-only and study-metadata baselines for reviewer comparison against imaging-only Ridge. |

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
- Do not describe binary low-VTI summaries as separately optimized classifiers.
- Do not overstate TAPSE precision given the smaller test set and selected alpha of 1000.

## Provenance Positioning Notes

- Use "mean-pooled all-clips study-level EchoPrime embeddings" for the primary imaging input.
- Define "frozen" as fixed EchoPrime encoder weights and fixed generated embeddings during downstream Ridge training.
- The trained Phase 2 model component was Ridge regression, not EchoPrime.
- If reviewers ask about Doppler or M-mode, the current code supports saying these were not explicitly localized or selected for the primary analysis. Exact retention of spectral Doppler or M-mode clips requires a separate DICOM/view audit.
- Keep `docs/phase2_embedding_provenance.md` aligned with any future Methods revision.

## Reviewer-Risk Notes

- Results should remain numerical and avoid clinical interpretation beyond the reported metrics.
- Discussion should acknowledge the wide Bland-Altman limits when interpreting LVOT VTI performance.
- ECHOVIEW analyses are best framed as limited subset sensitivities because of their smaller denominator and uncertainty.
- TAPSE is positive but secondary, smaller, and strongly regularized.
- Binary low-VTI AUROC values are exploratory and should be paired with the reported operating-point sensitivity and specificity.
- Future-work language should point to measurement-view localization, Doppler-specific LVOT VTI processing, TAPSE M-mode/RV-focused clip processing, and raw-DICOM or clip-level modeling as separate next steps.

## Reviewer Follow-Up Decision Logic

- If demographics-only performance is much worse than imaging-only, the manuscript can say the embedding model outperformed a leakage-safe non-image baseline.
- If demographics-only performance is similar to imaging-only, weaken imaging-specific language and frame the result as not clearly above simple metadata.
- Study/acquisition metadata baselines must be reported separately from demographics-only because clip counts and extraction success are image-pipeline metadata.
- If TAPSE `<17 mm` has adequate positive cases and reasonable AUROC, include it as a supplementary exploratory binary result.
- If TAPSE `<17 mm` is underpowered, mention it only in supplement or internal notes and do not imply a negative result.
- If Doppler/M-mode retention cannot be verified from aggregate metadata, keep current limitation language.
- If Doppler/M-mode clips are retained, clarify that all-clips embeddings may include measurement-relevant clips but were not localized to measurement traces.
