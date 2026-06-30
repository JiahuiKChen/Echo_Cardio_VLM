# Phase 2 Reviewer Handoff

## Start Here

- PR: <https://github.com/JiahuiKChen/Echo_Cardio_VLM/pull/1>
- Branch: `codex/phase2-stable-imaging-baselines`
- Summary: this PR adds the reproducible Phase 2 imaging-only TAPSE/LVOT VTI baseline workflow, verified aggregate results, manuscript text, table sources, figure workflows, and data-governance safeguards.

Recommended first files to read:

1. `docs/phase2_status_and_roadmap.md`
2. `docs/phase2_embedding_provenance.md`
3. `docs/phase2_manuscript_sections.md`
4. `docs/tables/phase2/`
5. `docs/phase2_pr_summary.md`

## What This PR Adds

- Phase 1 target feasibility, leakage, split-integrity, and ECHOVIEW join audit infrastructure.
- Stable-v2 Ridge baseline workflow with train-fit standardization, `svd` solver, validation-only alpha selection, bootstrap CIs, and restricted-output safeguards.
- ECHOVIEW LVOT VTI view-filtered sensitivity workflow.
- Aggregate-only verification workflow for manuscript-safe result extraction.
- Reviewer follow-up aggregate summaries for a study/acquisition-metadata baseline, TAPSE `<17 mm` thresholding, and Doppler/M-mode retention audit caveats.
- Restricted/SCC and local figure-generation workflows with governance boundaries.
- Manuscript sections, canonical table sources, and a DOCX table packet.
- EchoPrime embedding provenance and claim-boundary documentation.

## Main Scientific Takeaways

- LVOT VTI is the primary Phase 2 imaging-only endpoint.
- TAPSE is a cautious secondary endpoint.
- LVOT VTI all-clips stable-v2: test N 555, null MAE 4.54 cm, Ridge MAE 3.64 cm, R2 0.372.
- TAPSE all-clips stable-v2: test N 160, null MAE 3.79 mm, Ridge MAE 3.17 mm, R2 0.284.
- Hard-extreme exclusions did not materially change LVOT VTI or TAPSE results.
- ECHOVIEW analyses are limited subset sensitivities, not competing primary denominators.
- A5C-only 0.70 was skipped/underpowered because of insufficient training data.
- Study/acquisition metadata using only `n_clips` and `n_dicoms` performed near null for both LVOT VTI and TAPSE, below the EchoPrime embedding models.
- TAPSE `<17 mm` exploratory threshold summary: test N 160, positives 36, AUROC 0.789, sensitivity 0.472, specificity 0.944.

## Claim Boundary

- EchoPrime was used as a frozen feature extractor.
- Phase 2 trained only downstream Ridge regression.
- Clip-level 512-dimensional EchoPrime encoder embeddings were mean-pooled into study-level all-clips embeddings.
- This is not EchoPrime fine-tuning.
- This is not direct LVOT VTI extraction from spectral Doppler traces.
- This is not direct TAPSE extraction from M-mode or tricuspid-annular motion clips.
- Avoid measurement-grade automation and clinical-deployment claims.
- Do not call the `n_clips`/`n_dicoms` metadata baseline a demographics baseline; demographics were not evaluated because an approved demographics file was unavailable.
- The aggregate Doppler/M-mode retention audit could not classify retained clips reliably from available metadata, so it supports cautious limitation language rather than stronger measurement-view claims.

## Governance

- Committed files are scripts, docs, aggregate manuscript/table summaries, and table assets.
- Not committed: patient-level predictions, figure-ready CSVs, generated figures, SCC outputs, raw embeddings, logs, restricted manifests, or DUA-governed data.
- Local figure-ready CSVs remain restricted derived row-level data and should not be uploaded, committed, pasted into chat, or stored in cloud-synced folders.

## Suggested Review Checklist

- Verify no restricted artifacts are committed.
- Review Methods/Results language for correct claim boundaries.
- Confirm table values against the PR summary.
- Review whether TAPSE belongs in the main table or supplement.
- Review whether exploratory binary threshold results belong in main text or supplement.
- Review whether TAPSE `<17 mm` belongs in Supplementary Table S3 or a separate supplementary table.
- Review whether the study/acquisition-metadata baseline should be included as Supplementary Table S4.
- Decide whether a leakage-safe clinical covariate baseline is needed before submission.
- Decide whether richer Doppler/M-mode retention audit or measurement-view localization is needed before submission or can be future work.

## Known Remaining Decisions

- TAPSE placement.
- Exploratory binary threshold placement.
- TAPSE `<17 mm` exploratory binary placement.
- Study/acquisition-metadata baseline placement.
- Clinical covariate baseline.
- Demographics-only baseline if an approved demographics file becomes available.
- Richer Doppler/M-mode retention audit.
- Measurement-view localization future work.
- External validation feasibility.
- Coauthor figure/table review.
