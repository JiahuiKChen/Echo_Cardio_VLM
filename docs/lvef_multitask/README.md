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

Phase 1A aggregate SCC findings are recorded in `phase1a_scc_findings.md`. The decision checklist in `phase1b_pre_revalidation_lock.md` is not passed: model fitting, historical prediction regeneration, and new test-performance access remain unauthorized. The OpenEvidence requests are split between dependency/leakage adjudication and clinical thresholds/variability/margins; neither response will become authority until its sources are checked and clinician review is documented.

## Terminology

New documents use:

- `vision-only`: frozen EchoPrime video-encoder study representation;
- `structured-only`: allowed structured measurements after the applicable mask;
- `early fusion`: concatenated vision and structured predictors;
- an explicit panel version, such as `legacy29`, `strict21-v1`, or `pragmatic26-v1`;
- an explicit model/config version.

The E1/E2a/E2b/E3/E5 development labels are historical and should not appear in new scientific claims.

## Claim boundary

In scope: frozen-representation utility, multimodal incremental value, observed report-label prediction, and simulated report completion under prespecified masking.

Out of scope for this phase: direct pixel-level measurement automation, truth for genuinely missing labels, autonomous report generation, clinical deployment, causal utility, patient outcomes, perioperative risk prediction, and generative performance.

## Data governance

Git may contain aggregate counts, suppression-safe tables, documentation, configs, code, tests with synthetic data, and checksums of restricted authorities. Subject/study manifests, labels, predictions, embeddings, DICOM paths, logs, and SCC environment files remain restricted.
