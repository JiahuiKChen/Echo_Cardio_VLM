# Phase 2 Status and Roadmap

## 1. Current Branch and Milestone

- Branch: `codex/phase2-stable-imaging-baselines`
- Milestone: stable-v2 imaging-only baseline workflow, verified aggregate results, manuscript draft sections, figure workflow, and canonical manuscript table sources for LVOT VTI and TAPSE.
- Current manuscript emphasis: LVOT VTI all-clips stable-v2 as the primary imaging-only baseline; TAPSE all-clips stable-v2 as a cautious secondary endpoint.

## 2. What Phase 2 Accomplished

- Added stable-v2 imaging-only baseline scripts using frozen EchoPrime embeddings, train-fit standardization, Ridge regression with the `svd` solver, validation-only alpha selection, bootstrap confidence intervals, binary low-VTI summaries, and restricted-output governance controls.
- Added view-filtered embedding aggregation for ECHOVIEW subset sensitivity analyses.
- Added aggregate-only verification tooling that reads manuscript-safe aggregate outputs and excludes patient-level prediction files.
- Added restricted figure workflow tooling, including SCC-only figure generation and local plotting from minimal restricted derived figure-ready CSVs.
- Added manuscript-ready sections, internal notes, canonical Markdown/CSV table sources, and a DOCX table packet.
- Added EchoPrime embedding provenance documentation clarifying that Phase 2 used fixed study-level all-clips embeddings aggregated from successfully processed multiframe cine clips, with downstream Ridge regression as the trained model component.
- Added reviewer follow-up tooling for leakage-safe non-image baselines, TAPSE `<17 mm` aggregate binary summaries, and aggregate-only Doppler/M-mode retention auditing.
- Completed aggregate-only reviewer follow-up summaries: leakage-safe demographics and study/acquisition metadata baselines were evaluated, TAPSE `<17 mm` binary summary was extracted, and available DICOM metadata were insufficient to classify Doppler/M-mode retention reliably.

## 3. Verified Primary Result: LVOT VTI

- Target + embedding studies: 3,782
- Split: train 2,654; validation 573; test 555
- Null median MAE: 4.54 cm
- Ridge MAE: 3.64 cm
- Ridge R2: 0.372
- Ridge MAE 95% CI: 3.41-3.88 cm
- Ridge R2 95% CI: 0.30-0.43
- Exploratory low-VTI AUROC:
  - LVOT VTI <18 cm: 0.846
  - LVOT VTI <20 cm: 0.812

Interpretation status: primary manuscript-facing imaging-only baseline. The result supports moderate imaging-only LVOT VTI estimation signal, while the manuscript should avoid measurement-replacement language.

## 4. Verified Secondary Result: TAPSE

- Target + embedding studies: 1,131
- Split: train 787; validation 184; test 160
- Null median MAE: 3.79 mm
- Ridge MAE: 3.17 mm
- Ridge R2: 0.284
- Ridge MAE 95% CI: 2.80-3.56 mm
- Ridge R2 95% CI: 0.13-0.40
- Exploratory TAPSE `<17 mm` summary:
  - test positives 36 of 160; prevalence 0.225
  - AUROC 0.789; average precision 0.638
  - operating-point sensitivity 0.472; specificity 0.944

Interpretation status: cautious secondary endpoint. The selected alpha was 1000, so the manuscript should describe TAPSE as strongly regularized and avoid precision overclaims.

## 5. Sensitivity Analyses

- LVOT VTI hard-extreme exclusion: Ridge MAE 3.62 cm; R2 0.376.
- TAPSE hard-extreme exclusion: Ridge MAE 3.17 mm; R2 0.284.
- ECHOVIEW LVOT VTI analyses are limited subset analyses, not competing primary denominators.
- ECHOVIEW A5C-only 0.70 was skipped/underpowered because of insufficient training data and should not be interpreted as a negative result.
- Leakage-safe non-image baselines:
  - Demographics-only used approximate age at echo and sex; race/ethnicity was not included.
  - LVOT VTI demographics-only: MAE 4.47 cm; R2 0.043.
  - LVOT VTI study/acquisition metadata only (`n_clips`, `n_dicoms`): MAE 4.58 cm; R2 0.008.
  - LVOT VTI demographics + study/acquisition metadata: MAE 4.48 cm; R2 0.048.
  - TAPSE demographics-only: MAE 3.80 mm; R2 -0.024.
  - TAPSE study/acquisition metadata only: MAE 3.77 mm; R2 0.0004.
  - TAPSE demographics + study/acquisition metadata: MAE 3.79 mm; R2 -0.018.
- Doppler/M-mode retention audit summarized 311,043 readable DICOM audit rows, 170,600 multiframe candidates, 170,600 successfully extracted clips, 191,993 successfully embedded clips, and 4,696 study embeddings. Available metadata keyword fields did not reliably classify Doppler, spectral Doppler, color Doppler, M-mode, or 2D/cine categories, so absence of keyword matches should not be interpreted as absence of those acquisition types.

## 6. Figures and Tables

Committed table/manuscript assets:

- `docs/phase2_manuscript_sections.md`
- `docs/phase2_internal_notes.md`
- `docs/phase2_embedding_provenance.md`
- `docs/phase2_reviewer_followup_runbook.md`
- `docs/tables/phase2/table1_main_continuous_performance.{md,csv}`
- `docs/tables/phase2/tableS1_hard_extreme_robustness.{md,csv}`
- `docs/tables/phase2/tableS2_echoview_sensitivity.{md,csv}`
- `docs/tables/phase2/tableS3_binary_low_vti.{md,csv}`
- `docs/tables/phase2/tableS4_nonimage_metadata_baseline.{md,csv}`
- `docs/tables/phase2/phase2_tables_for_manuscript.docx`

Local/generated figure assets are intentionally not committed. Final reviewed figures exist outside the repository and should be handled as manuscript-export artifacts, not source-control assets, unless explicitly approved later.

## 7. Data Governance Status

Committed files include source scripts, runbooks, aggregate-only summaries in manuscript/table form, and manuscript text. Patient-level predictions, restricted demographics feature CSVs, figure-ready CSVs, generated figures, SCC outputs, restricted manifests, raw embeddings, logs, and DUA-governed data were not committed.

Minimal local figure-ready CSVs remain restricted derived row-level data. They should not be uploaded, committed, pasted into chat, stored in cloud-synced folders, or shared outside approved secure storage.

## 8. Remaining Manuscript Tasks

- Decide whether TAPSE remains in the main table or moves fully to the supplement.
- Decide whether exploratory binary threshold AUROC summaries stay in the main text or supplement.
- Decide whether the non-image baseline table belongs in the main supplement table sequence or an appendix.
- Decide whether TAPSE `<17 mm` exploratory binary summaries belong in Supplementary Table S3 or a separate binary threshold table.
- Regenerate the optional DOCX table packet from canonical Markdown/CSV sources after final table placement is decided.
- Integrate Phase 2 methods/results into the full manuscript.
- Confirm journal-specific table and figure formatting requirements.
- Obtain coauthor review of figures, tables, and interpretation.
- Decide whether broader leakage-safe clinical covariates beyond age and sex are needed before submission.
- Decide whether external validation is feasible now or should be explicitly deferred to limitations/future work.

## 9. Optional Future Analyses

- Broader leakage-safe clinical covariate baseline beyond age and sex, if scientifically justified.
- Additional calibration analysis.
- Expanded denominator documentation for each target and sensitivity subset.
- Additional Doppler/M-mode retention audit using richer metadata, view labels, or manual review if measurement-view claims become central.
- Measurement-view localization audit comparing all-clips study embeddings with selected measurement-relevant clips.
- Doppler-specific LVOT VTI pipeline that explicitly identifies or processes spectral Doppler clips.
- TAPSE-focused clip pipeline using RV-focused, A4C, M-mode, or other tricuspid-annular motion-relevant clips where available.
- Raw-DICOM, clip-level, or pixel-level model for direct measurement automation as a separate study.
- External validation if data access permits.
- Model comparison beyond Ridge only if scientifically justified and leakage-safe.

## 10. Recommended Next Decision Points

1. Choose final placement for TAPSE: main text, supplement, or hybrid.
2. Choose final placement for exploratory binary threshold summaries.
3. Decide whether the current Ridge baseline and non-image baselines are sufficient for manuscript submission or whether broader clinical covariates are required.
4. Run coauthor review of Figure 1, Supplementary Figure S1, Table 1, and Supplementary Tables S1-S4.
5. Freeze data-governance language before exporting final manuscript packages.

## Suggested GitHub Issues

- Finalize manuscript table placement.
- Review Phase 2 figures with coauthors.
- Decide on broader clinical covariate baseline.
- Prepare final manuscript package.
- External validation planning.
