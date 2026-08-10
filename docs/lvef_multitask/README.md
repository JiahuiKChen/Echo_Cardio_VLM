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

Phase 1A aggregate findings are recorded in `phase1a_scc_findings.md`, the Phase 1B provenance follow-up is interpreted in `phase1b_scc_followup_findings.md`, the supplied Phase 1C transcript is interpreted in `phase1c_scc_execution_findings.md`, and the completed model-independent SCC remediation is recorded in `phase1d_scc_execution_findings.md`. The Phase 1D controlling checklist remains `phase1d_pre_embedding_lock.md`; `phase1d_embedding_go_no_go.md` records why historical artifacts cannot establish embedding authority. Phase 1E-A is governed by `phase1e_reconstruction_preflight.md`, `scc_phase1e_reconstruction_commands.md`, the [aggregate-only smoke findings](phase1e_reconstruction_smoke_findings.md), and the versioned config. Phase 1E-B/C adds the [institutional restricted-agent authority](institutional_restricted_ai_authorization.md), [two-mode secure-analysis bridge](secure_analysis_bridge.md), [storage migration plan](scc_storage_migration_plan.md), [storage/cost contract](c3_storage_and_cost_plan.md), [SCC metadata-only commands](scc_phase1ebc_commands.md), and [full-C3 pre-authorization gate](phase1e_full_c3_pre_authorization.md).

The Phase 1E-A four-training-study technical smoke passed: 252 public objects were transport-verified and DICOM-header readable, 123 multiframe candidates were pixel-decoded, extracted, and encoded twice, three study vectors were pooled twice, all five reproducibility comparisons were exact, and the independent preservation second pass passed. This is canary implementation evidence only. The full C3 and confirmatory locks are not passed; all 32 historical duplicate groups remain quarantined, retained artifacts cannot support Paths A, C1, or C2, and C3 remains the conditional clean-provenance preference. Phase 1E-B/C resolves `lvef` as a separate exact-name label authority rather than inventing a canonical-map row, implements approved direct restricted-agent inspection separately from reviewed export, and prepares fail-closed source/quota/metadata audits and an eight-question human signoff packet. The live GCS metadata preflight is complete SCC execution evidence whose aggregate authorities are recorded in Git; patient-level/source-object details remain restricted. Phase 1E-E implements the prospective production orchestration without executing it and seals a post-expansion capacity successor. Nine technical dispositions and human echocardiographer signoff remain separate modeling gates. No targeted OpenEvidence prompt is currently justified. Full-cohort reconstruction, model fitting, prediction generation, new confirmatory test-performance access, and performance-guided design remain unauthorized.

The current selected-source inventory, Autoclass, and rate-explicit cost adjudications have passed. The owner-accepted requester-pays estimates ($136.101850/$142.906680/$171.488015) and SCC storage estimate are frozen for planning; no further cost verification is required. This closes only the planning-cost gate. Immutable parent research attempt `lvef_multitask_phase1ee_post_expansion_capacity_attempt_001` produced `lvef_c3_live_quota.summary.json` (2,257 bytes; SHA-256 `267bf03d8f059b4a71ebe0754015af4a710edea37c060e3e392642e1ad335d71`). Composite successor `lvef_multitask_phase1ee_post_expansion_capacity_attempt_002` is bound to implementation commit `0800a0b4de93911cc39467acf2460a8d5ed6135a`; its 5,003-byte aggregate has SHA-256 `28fad54a68f84165cb8340c3e666de84e1f6efc6bf20b146bc7bc006d9d4171c`. It hash-revalidated the parent research authority and added only contemporaneous control-tier evidence. The research tier passes byte quota, file quota, underlying-filesystem, minimum-quota, and 200-GB-reserve gates: quota/usage/available are 1,989,000,000,000 / 150,387,011,072 / 1,838,612,988,928 bytes, physical availability is 2,092,672,483,328 bytes, and quota/physical slack after the frozen peak are 377,357,923,668 / 431,417,418,068 bytes. The backed control tier is the remaining capacity blocker: its 11,000,000,000-byte quota has 10,959,364,608 bytes used and only 40,635,392 bytes available, although its file-count gate passes. The preferred adjustment is 50 GB backed and 1,950 GB research; 25/1,975 GB is the minimum safe option. The owner-attested administrative composition keeps the purchased 1-TB SAAS allocation on the research tier; machine evidence establishes the exact total quota but not that funding composition. Full C3 remains `NO_GO` pending an active control-tier adjustment with a fresh receipt, verified backup/recovery evidence, a final current-commit authority packet/launch envelope, and explicit owner transfer authorization.

The production environment is now a closed split-runtime authority: the
validated EchoPrime Python is unchanged, while a separately checksummed,
isolated Cloud-SDK-bundled Python 3.14 process supplies compiled CRC32C through
one persistent worker per download batch. The immutable first lock attempt
failed before creating a production attempt root; a fresh no-clobber lock is
required after the repair.

## Terminology

New documents use:

- `vision-only`: frozen EchoPrime video-encoder study representation;
- `structured-only`: allowed structured measurements after the applicable mask;
- `early fusion`: concatenated vision and structured predictors;
- an explicit construct and panel version: historical `legacy29`, strict leakage-minimized measurement, report completion with target-family masking, or pragmatic same-report completion;
- an explicit model/config version.

The E1/E2a/E2b/E3/E5 development labels are historical and should not appear in new scientific claims.

## Claim boundary

In scope: frozen-representation utility, multimodal incremental value, observed report-label prediction, and simulated report completion under prespecified masking.

Out of scope for this phase: direct pixel-level measurement automation, truth for genuinely missing labels, autonomous report generation, clinical deployment, causal utility, patient outcomes, perioperative risk prediction, and generative performance.

## Data governance

Git may contain aggregate counts, suppression-safe tables, documentation, configs, code, tests with synthetic data, and checksums of restricted authorities. Subject/study manifests, labels, predictions, embeddings, DICOM paths, logs, and SCC environment files remain restricted.
