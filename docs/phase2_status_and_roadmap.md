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

Interpretation status: cautious secondary endpoint. The selected alpha was 1000, so the manuscript should describe TAPSE as strongly regularized and avoid precision overclaims.

## 5. Sensitivity Analyses

- LVOT VTI hard-extreme exclusion: Ridge MAE 3.62 cm; R2 0.376.
- TAPSE hard-extreme exclusion: Ridge MAE 3.17 mm; R2 0.284.
- ECHOVIEW LVOT VTI analyses are limited subset analyses, not competing primary denominators.
- ECHOVIEW A5C-only 0.70 was skipped/underpowered because of insufficient training data and should not be interpreted as a negative result.

## 6. Figures and Tables

Committed table/manuscript assets:

- `docs/phase2_manuscript_sections.md`
- `docs/phase2_internal_notes.md`
- `docs/tables/phase2/table1_main_continuous_performance.{md,csv}`
- `docs/tables/phase2/tableS1_hard_extreme_robustness.{md,csv}`
- `docs/tables/phase2/tableS2_echoview_sensitivity.{md,csv}`
- `docs/tables/phase2/tableS3_binary_low_vti.{md,csv}`
- `docs/tables/phase2/phase2_tables_for_manuscript.docx`

Local/generated figure assets are intentionally not committed. Final reviewed figures exist outside the repository and should be handled as manuscript-export artifacts, not source-control assets, unless explicitly approved later.

## 7. Data Governance Status

Committed files include source scripts, runbooks, aggregate-only summaries in manuscript/table form, and manuscript text. Patient-level predictions, figure-ready CSVs, generated figures, SCC outputs, restricted manifests, raw embeddings, logs, and DUA-governed data were not committed.

Minimal local figure-ready CSVs remain restricted derived row-level data. They should not be uploaded, committed, pasted into chat, stored in cloud-synced folders, or shared outside approved secure storage.

## 8. Remaining Manuscript Tasks

- Decide whether TAPSE remains in the main table or moves fully to the supplement.
- Decide whether binary low-VTI AUROC stays in the main text or supplement.
- Integrate Phase 2 methods/results into the full manuscript.
- Confirm journal-specific table and figure formatting requirements.
- Obtain coauthor review of figures, tables, and interpretation.
- Decide whether a leakage-safe clinical covariate baseline is needed before submission.
- Decide whether external validation is feasible now or should be explicitly deferred to limitations/future work.

## 9. Optional Future Analyses

- Leakage-safe clinical covariate baseline.
- Additional calibration analysis.
- Expanded denominator documentation for each target and sensitivity subset.
- External validation if data access permits.
- Model comparison beyond Ridge only if scientifically justified and leakage-safe.

## 10. Recommended Next Decision Points

1. Choose final placement for TAPSE: main text, supplement, or hybrid.
2. Choose final placement for binary low-VTI summaries.
3. Decide whether the current Ridge baseline is sufficient for manuscript submission or whether a clinical covariate baseline is required.
4. Run coauthor review of Figure 1, Supplementary Figure S1, Table 1, and Supplementary Tables S1-S3.
5. Freeze data-governance language before exporting final manuscript packages.

## Suggested GitHub Issues

- Finalize manuscript table placement.
- Review Phase 2 figures with coauthors.
- Decide on clinical covariate baseline.
- Prepare final manuscript package.
- External validation planning.
