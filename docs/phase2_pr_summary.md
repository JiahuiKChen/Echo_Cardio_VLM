# PR Summary: Phase 2 TAPSE/LVOT VTI Imaging Baseline Results and Manuscript Assets

## Branch

`codex/phase2-stable-imaging-baselines`

## Suggested PR Title

Phase 2 TAPSE/LVOT VTI imaging baseline results and manuscript assets

## Purpose

This branch adds the Phase 1/Phase 2 infrastructure and manuscript assets for imaging-only LVOT VTI and TAPSE baseline analyses using frozen EchoPrime echocardiography embeddings. It stabilizes the modeling workflow, verifies aggregate-only results, creates figure/table workflows, and drafts manuscript-ready sections.

## Major Changes

- Phase 1 target feasibility, leakage, and ECHOVIEW join audit scripts.
- Stable-v2 imaging-only baseline script with train-fit standardization, Ridge `svd` solver, validation-only alpha selection, bootstrap CIs, hard-extreme exclusion, binary low-VTI summaries, and restricted patient-output safeguards.
- ECHOVIEW view-filtered embedding aggregation script.
- Aggregate-only Phase 2 results verifier.
- Restricted and local figure-generation workflows.
- Manuscript-style Methods, Results, Discussion, Limitations, figure captions, internal notes, and canonical table sources.
- DOCX table packet generated from canonical Markdown/CSV table sources.

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
- Decide whether binary low-VTI summaries stay in main text or supplement.
- Integrate Phase 2 sections into the full manuscript.
- Confirm final journal-specific figure and table formatting.
- Decide whether a leakage-safe clinical covariate baseline is needed before submission.
- Obtain coauthor review of the final figure/table interpretation.

## Suggested Reviewer Checklist

- Confirm no restricted data artifacts are committed.
- Review stable-v2 modeling defaults and validation-only alpha selection.
- Review table values against verified aggregate outputs.
- Review manuscript language for avoiding measurement-replacement claims.
- Review ECHOVIEW analyses as limited subset sensitivities rather than primary denominators.
- Review whether the DOCX table packet should remain tracked or be regenerated during manuscript packaging.
