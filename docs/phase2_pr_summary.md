# PR Summary: Phase 2 TAPSE/LVOT VTI Imaging Baseline Results and Manuscript Assets

## Branch

`codex/phase2-stable-imaging-baselines`

## Suggested PR Title

Phase 2 TAPSE/LVOT VTI imaging baseline results and manuscript assets

## Purpose

This branch adds the Phase 1/Phase 2 infrastructure and manuscript assets for imaging-only LVOT VTI and TAPSE baseline analyses using frozen EchoPrime echocardiography embeddings. It stabilizes the modeling workflow, verifies aggregate-only results, creates figure/table workflows, and drafts manuscript-ready sections.

## Major Changes

- Phase 1 target feasibility, leakage, and ECHOVIEW join audit scripts.
- Stable-v2 imaging-only baseline script with train-fit standardization, Ridge `svd` solver, validation-only alpha selection, bootstrap CIs, hard-extreme exclusion, exploratory binary threshold summaries, and restricted patient-output safeguards.
- ECHOVIEW view-filtered embedding aggregation script.
- Aggregate-only Phase 2 results verifier.
- Restricted and local figure-generation workflows.
- Manuscript-style Methods, Results, Discussion, Limitations, figure captions, internal notes, and canonical table sources.
- DOCX table packet generated from canonical Markdown/CSV table sources.
- EchoPrime embedding provenance and manuscript-claim boundary documentation.
- Reviewer follow-up summaries for study/acquisition-metadata baselines, exploratory TAPSE `<17 mm` thresholding, and aggregate Doppler/M-mode retention auditing.

## Key Verified Results

Primary LVOT VTI all-clips stable-v2:

- 3,782 target + embedding studies; train 2,654, validation 573, test 555.
- Null MAE 4.54 cm; Ridge MAE 3.64 cm; Ridge R2 0.372.
- Ridge MAE 95% CI 3.41-3.88 cm; Ridge R2 95% CI 0.30-0.43.
- Exploratory low-VTI AUROC 0.846 for LVOT VTI <18 cm and 0.812 for LVOT VTI <20 cm.

Secondary TAPSE all-clips stable-v2:

- 1,131 target + embedding studies; train 787, validation 184, test 160.
- Null MAE 3.79 mm; Ridge MAE 3.17 mm; Ridge R2 0.284.
- Ridge MAE 95% CI 2.80-3.56 mm; Ridge R2 95% CI 0.13-0.40.

Sensitivity analyses:

- LVOT hard-extreme exclusion: MAE 3.62 cm; R2 0.376.
- TAPSE hard-extreme exclusion: MAE 3.17 mm; R2 0.284.
- ECHOVIEW analyses are limited subset sensitivities; A5C-only 0.70 was skipped/underpowered.
- Study/acquisition-metadata Ridge baseline using only `n_clips` and `n_dicoms` performed near null: LVOT VTI MAE 4.58 cm, R2 0.008; TAPSE MAE 3.77 mm, R2 0.0004.
- Exploratory TAPSE `<17 mm` threshold summary: 36 positives among 160 test studies, AUROC 0.789, average precision 0.638, sensitivity 0.472, specificity 0.944.

## Provenance and Claim Boundary

- EchoPrime was used as a frozen feature extractor. Encoder weights and generated embeddings were fixed during Phase 2 downstream modeling.
- The trained supervised component was downstream Ridge regression, not EchoPrime fine-tuning.
- Clip-level 512-dimensional EchoPrime encoder embeddings were mean-pooled into study-level all-clips embeddings for the primary analyses.
- The embedding pipeline used successfully processed multiframe cine clips; still-frame DICOMs were excluded by the multiframe cine filter.
- Aggregate Doppler/M-mode retention audit counted 311,043 readable DICOM audit rows, 170,600 multiframe candidates, 170,600 successfully extracted clips, 191,993 successfully embedded clips, and 4,696 study embeddings. Available DICOM metadata fields were insufficient to classify retained clips reliably as Doppler, M-mode, or 2D/cine; absence of keyword matches should not be interpreted as absence of those acquisition types.
- Manuscript language explicitly avoids claims of direct LVOT VTI extraction from spectral Doppler traces, direct TAPSE extraction from M-mode/tricuspid-annular motion clips, measurement-grade automation, or clinical deployment readiness.

## Files Added or Changed

- Audit and modeling scripts under `scripts/`.
- Phase 2 runbooks and manuscript documents under `docs/`.
- Canonical Phase 2 tables under `docs/tables/phase2/`.
- Governance protections in `.gitignore`.

## Governance Checks

Committed files are source code, runbooks, aggregate-only manuscript/table summaries, and manuscript text. The branch intentionally excludes patient-level prediction CSVs, restricted figure-ready CSVs, generated figures, SCC output directories, raw embeddings, logs, restricted manifests, and DUA-governed data.

Tracked-file name audit did not identify committed patient-level outputs or restricted artifacts. Source files may contain terms such as `prediction`, `subject_id`, or `dicom` in code and safety checks; those are not data artifacts.

## Intentionally Not Included

- Patient-level predictions.
- Figure-ready restricted derived CSVs.
- Generated PNG/PDF figure packets.
- SCC logs or raw outputs.
- Raw embeddings or NPZ files.
- DICOM paths, image paths, manifests, or patient identifiers.

## Remaining TODOs

- Decide whether TAPSE stays in the main table or moves fully to the supplement.
- Decide whether exploratory binary threshold summaries stay in main text or supplement.
- Integrate Phase 2 sections into the full manuscript.
- Confirm final journal-specific figure and table formatting.
- Consider demographics-only baseline if an approved demographics file becomes available.
- Consider richer Doppler/M-mode retention or measurement-view localization audit if measurement-view claims become important.
- Consider measurement-view localization or raw-DICOM/clip-level direct measurement studies as future work.
- Decide whether a leakage-safe clinical covariate baseline is needed before submission.
- Obtain coauthor review of the final figure/table interpretation.

## Suggested Reviewer Checklist

- Confirm no restricted data artifacts are committed.
- Review stable-v2 modeling defaults and validation-only alpha selection.
- Review table values against verified aggregate outputs.
- Review EchoPrime provenance wording and confirm no fine-tuning or direct measurement claims are implied.
- Review manuscript language for avoiding measurement-replacement claims.
- Review ECHOVIEW analyses as limited subset sensitivities rather than primary denominators.
- Review study/acquisition-metadata baseline wording and confirm it is not described as a demographics baseline.
- Review TAPSE `<17 mm` threshold summary as exploratory and supplementary.
- Review Doppler/M-mode audit caveat and confirm direct measurement-view claims remain excluded.
- Review whether the DOCX table packet should remain tracked or be regenerated during manuscript packaging.
