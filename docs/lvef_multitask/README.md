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

The Phase 1E-A four-training-study technical smoke passed: 252 public objects were transport-verified and DICOM-header readable, 123 multiframe candidates were pixel-decoded, extracted, and encoded twice, three study vectors were pooled twice, all five reproducibility comparisons were exact, and the independent preservation second pass passed. This is canary implementation evidence only. The full C3 and confirmatory locks are not passed; all 32 historical duplicate groups remain quarantined, retained artifacts cannot support Paths A, C1, or C2, and C3 remains the conditional clean-provenance preference. Phase 1E-B/C resolves `lvef` as a separate exact-name label authority rather than inventing a canonical-map row, implements approved direct restricted-agent inspection separately from reviewed export, and prepares fail-closed source/quota/metadata audits and an eight-question human signoff packet. The live GCS metadata preflight is complete SCC execution evidence whose aggregate authorities are recorded in Git; patient-level/source-object details remain restricted. Phase 1E-D sealed the exact current quota/filesystem evidence, which failed the resource gates; activation of the additional terabyte and a post-activation recapture remain required. Nine technical dispositions and human echocardiographer signoff remain separate modeling gates. No targeted OpenEvidence prompt is currently justified. Full-cohort reconstruction, model fitting, prediction generation, new confirmatory test-performance access, and performance-guided design remain unauthorized.

The current selected-source inventory, Autoclass, and rate-explicit cost adjudications have passed. For Phase 1E-D, the owner accepted the frozen requester-pays estimates ($136.101850/$142.906680/$171.488015) and the owner-provided SCC storage estimate for planning; no further cost verification is required. This closes only the planning-cost gate. The sealed SCC evidence capture passed provenance validation but its capacity gate failed: the research quota is 989,000,000,000 bytes versus a 1,811,642,076,332-byte minimum, and filesystem availability independently has a 643,503,045,292-byte deficit. The separate offline lock passed only as `PASS_SPECIFICATION_ONLY_EXECUTION_UNIMPLEMENTED`; it implemented and authorized no production action. The additional one-terabyte allocation, checksum-bound backup/migration authority, production implementation, explicit DICOM-transfer authorization, full C3, and modeling remain blocked. See `phase1ed_pretransfer_lock_findings.md` and the historical no-rerun `scc_phase1ed_pretransfer_commands.md` execution record.

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
