# LVEF and multitask revalidation project

This directory governs the LVEF/multitask revalidation project on branch `codex/lvef-multitask-revalidation`, based on commit `23c74cc`. It is separate from the LVOT VTI/TAPSE branch.

## Scientific direction

The provisional manuscript concept is:

> Leakage-aware multimodal quantitative echocardiographic report completion using frozen whole-study EchoPrime representations and available structured measurements.

The one-study-per-subject cohort is primary. The historical LVEF <40% endpoint remains the binary clinical anchor. The primary revalidation must use identical modality denominators, train-only preprocessing, validation-only model selection, target-family masking, and paired subject-level inference.

## Authority hierarchy

1. `accepted_abstract_version10_verbatim.txt` is the immutable accepted-text authority.
2. `docs/results_snapshot/2026-04-01_fullscale/` is the immutable historical aggregate-results authority.
3. Scripts present at `23c74cc` document the historical implementation but do not by themselves prove which SCC command or artifact instance generated every result.
4. New revalidation outputs must be dated, checksum-manifested, aggregate-only in Git, and clearly separated from historical results.
5. Poster and manuscript artifacts must state whether a number is historical or post-acceptance revalidation.

The historical snapshot must never be overwritten or backfilled. New results belong under a new dated snapshot directory after the statistical analysis plan is locked.

## Current gate

Phase 1A aggregate findings are recorded in `phase1a_scc_findings.md`, the Phase 1B provenance follow-up is interpreted in `phase1b_scc_followup_findings.md`, the supplied Phase 1C transcript is interpreted in `phase1c_scc_execution_findings.md`, and the completed model-independent SCC remediation is recorded in `phase1d_scc_execution_findings.md`. The Phase 1D controlling checklist remains `phase1d_pre_embedding_lock.md`; `phase1d_embedding_go_no_go.md` records why historical artifacts cannot establish embedding authority. Phase 1E-A is governed by `phase1e_reconstruction_preflight.md`, `scc_phase1e_reconstruction_commands.md`, the [aggregate-only smoke findings](phase1e_reconstruction_smoke_findings.md), and the versioned config.

The Phase 1E-A four-training-study technical smoke passed: 252 public objects were transport-verified and DICOM-header readable, 123 multiframe candidates were pixel-decoded, extracted, and encoded twice, three study vectors were pooled twice, all five reproducibility comparisons were exact, and the independent preservation second pass passed. This is canary implementation evidence only. The full C3 and confirmatory locks are not passed; all 32 historical duplicate groups remain quarantined, retained artifacts cannot support Paths A, C1, or C2, and C3 remains the conditional clean-provenance preference. The clinical packet passed safely with 29 of 30 exact targets present; the separate `lvef` authority, eight clinician questions, nine technical reviews, dependency registry, and task panels remain open for Phase 1E-B. No targeted OpenEvidence prompt is currently justified. Full-cohort reconstruction, model fitting, prediction generation, new confirmatory test-performance access, and performance-guided design remain unauthorized.

## Terminology

New documents use:

- `vision-only`: frozen EchoPrime video-encoder study representation;
- `structured-only`: allowed structured measurements after the applicable mask;
- `early fusion`: concatenated vision and structured predictors;
- an explicit construct and panel version: historical `legacy29`, strict leakage-minimized measurement, target-family-masked report completion, or pragmatic same-report completion;
- an explicit model/config version.

The E1/E2a/E2b/E3/E5 development labels are historical and should not appear in new scientific claims.

## Claim boundary

In scope: frozen-representation utility, multimodal incremental value, observed report-label prediction, and simulated report completion under prespecified masking.

Out of scope for this phase: direct pixel-level measurement automation, truth for genuinely missing labels, autonomous report generation, clinical deployment, causal utility, patient outcomes, perioperative risk prediction, and generative performance.

## Data governance

Git may contain aggregate counts, suppression-safe tables, documentation, configs, code, tests with synthetic data, and checksums of restricted authorities. Subject/study manifests, labels, predictions, embeddings, DICOM paths, logs, and SCC environment files remain restricted.
