# Phase 1C pre-revalidation lock

## Decision

**The Phase 1C lock is not passed. Confirmatory model fitting, historical prediction regeneration, embedding regeneration, and confirmatory test-performance access are not authorized.**

This record converts the aggregate Phase 1B SCC follow-up and critically reviewed OpenEvidence inputs into a prefit gate. It preserves accepted ASA abstract version 10 verbatim and preserves exact historical `lvef < 40` as the primary binary endpoint. No historical result is rewritten.

The detailed evidence remains separated by role: `phase1b_scc_followup_findings.md` and `artifact_lineage.md` record aggregate provenance; `embedding_authority_decision.md` compares remediation paths; the OpenEvidence review documents and draft registries record clinical evidence and unresolved metadata; `statistical_analysis_plan.md` and `configs/lvef_multitask_revalidation.yaml` record prospective analysis choices. None alone authorizes execution.

Statuses mean:

- `DISPOSITIONED`: a historical defect has been classified and bounded, but the artifact has not been made valid;
- `PROVISIONAL`: a design rule is supported but must still pass implementation/identity checks;
- `PARTIAL`: some required specification exists, but the gate is incomplete;
- `BLOCKED`: evidence or authorization required for confirmatory use is absent;
- `PASS`: sufficient evidence is recorded for this gate. No single `PASS` authorizes a run.

## Gate table

| Required gate | Status | Evidence and present decision | Required closure evidence | What remains blocked |
|---|---|---|---|---|
| Historical freeze disposition | `DISPOSITIONED` | All 14 manifest lines have invalid relative paths and 13 pack files are unlisted. Zero byte mismatches were reported, but zero entries were path-valid and checksum-comparable; absence of byte mismatch is therefore not proven. The old pack is immutable, not fully checksum-validated, and not a future preservation authority. | No repair of the old pack. Independently checksum every source selected for a new run and issue a new safe-relative-path manifest. | Any claim that the old SCC pack is checksum-validated; use of that pack alone as new-run input authority. This limitation does not itself prohibit a new revalidation with independently validated sources. |
| 9,605 payload findings | `PASS` | After canonical normalization, residual full-row tuple mismatches are zero. The apparent differences are concentrated in `embedding_l2_norm` numeric serialization plus expected `embedding_idx` rewriting. | Retain aggregate evidence and regression tests; do not reopen based on the original unnormalized count alone. | Nothing independently. These 9,605 findings are not the remaining scientific blocker. |
| 32 duplicate clip keys | `BLOCKED` | All 32 are in `batch_000` and occur in both component and merged manifests. Their physical/content identity and cause are not adjudicated. | Restricted diagnostic classifying exact repeated row, same clip embedded twice, nonunique-key collision between different clips, merge/reindex defect, or other; aggregate-only reason counts; deterministic resolution and post-resolution identity checks. | Historical merged clip/study embedding authority and all vision/fusion confirmatory input. |
| Selected-cohort canonical clip authority | `BLOCKED` | Current mixed store contains 171 prior/nonselected studies. A canonical one-row-per-physical-clip selected-cohort inventory has not passed source/extracted hash and mapping validation. | Validate selected ownership, source-to-extracted mapping and available hashes/shapes; resolve duplicates; exclude 171 nonselected studies; record canonical inventory checksum without exporting rows to Git. | Clean selected-cohort embedding construction. |
| Embedding-regeneration decision | `BLOCKED` | Preferred path C is conditional: rebuild a selected-only clip and 512-dimensional mean-pooled study store from validated extracted cine clips under a pinned checkpoint/environment. Path A is permitted only if all 32 are exact/content-identical duplicates; path B if nonidentical or key-colliding. | Completed duplicate and extracted-authority audits, resource approval, written owner authorization, locked command/config/checksums. | Any re-embedding or study-store rebuild. |
| Checkpoint and environment authority | `BLOCKED` | Current `echo_prime_encoder.pt` identity is known: 138,642,379 bytes, SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`. Historical checkpoint use, package/Python/PyTorch/CUDA/cuDNN environment, and source commit are not proven. | For a new rebuild, use this exact hash unless an independently verified official checkpoint differs; capture source commit, command, packages, CUDA/cuDNN, GPU, scheduler job, and timestamps. Formally preserve the historical-link limitation. | Historical exact-reproduction claim and new embedding use until a future environment/run identity is locked. |
| Imaging eligibility | `PROVISIONAL` | Five selected studies (3 train, 1 validation, 1 test) are readable but have `NO_MULTIFRAME_CINE_CANDIDATE`; all have at least one `legacy29` label and three have numeric exact-raw LVEF. Under the historical multiframe rule they are imaging-ineligible, not unexplained downstream failures. Primary all-modality comparisons exclude them identically. | Implement rule as “at least one validated canonical multiframe cine clip”; pass aggregate split/label/common-denominator checks against the final canonical clip store. | Final cohort manifest and paired denominators, but not the provisional scientific rule. |
| Common modality denominators | `BLOCKED` | Primary vision, structured, and fusion estimands require exact same ordered subject-study-target rows. A structured full-availability sensitivity may retain the five imaging-ineligible studies, but it is unpaired and separately labeled. | Fresh prefit dry run with zero ID, pair, ownership, split, target, label, and multiplicity mismatches for every locked target/split; aggregate checksums only. | Paired modality and incremental-value claims. |
| Raw alias, definition, and unit review | `BLOCKED` | OpenEvidence output is corrupted in places and cannot resolve project-specific raw descriptions or units. Unknown or star-mangled names are not registry authority. | Restricted metadata packet plus explicit clinician adjudication of aliases, definitions, units, methods/views/timing, indexing, and formula candidates. | Final feature masks, target units, native-unit margins, and final task dispositions. |
| Clinical dependency registry | `BLOCKED` | Formula and clinical-family hypotheses exist, but not every proposed relationship has verified source support or project metadata. Broad correlation families are not formula dependency sets. | Allowlist-normalized, clinician-reviewed registry with evidence tiers and verified citations; unresolved relationships fail closed. | Leakage-minimized and target-family-masked fitting. |
| Task panels | `BLOCKED` | Strict, family-masked, and pragmatic constructs are distinct but provisional. LVEF is a separate anchor. BSA, height, SBP, DBP, and HR are context-only or a separate metadata benchmark, not primary echo-measurement macro targets. | Before/after disposition for every target after restricted clinical review; versioned exact membership and masks; support gates pass without performance selection. | Primary multitask macro estimand. |
| Thresholds and margins | `PARTIAL` | Historical primary `<40`; secondary `<=40` and `<50`. LVEF tolerances 5 points (4/8 sensitivity), paired-MAE margin 1 point (0.5/2 sensitivity), and descriptive bands 35%–45% (37%–43 sensitivity) are prespecified `EXPERT_INFERENCE`, not guideline equivalence/MCID. | Before test access, aggregate `n(lvef == 40.0)` by split for selected-preimaging and primary-common-imaging-eligible scopes, using exact parsed numeric equality after the historical subject/measurement median. Verify LVEF unit/method mix. Non-LVEF native-unit margins remain unresolved until unit/definition review; explicit “no defensible margin” is acceptable. | Any non-LVEF margin/tie claim and final endpoint registry checksum. |
| Model-independent statistical plan | `PARTIAL` | `statistical_analysis_plan.md` records cohort, masking, preprocessing, support, model-selection grids, binary estimands, calibration, operating point, metrics, bootstrap, multiplicity, missingness, and subgroup boundaries. All choices are performance-independent. | Reconcile the final clinical panels/registry and provenance decisions; validate implementation; record final SAP checksum and version. | Final SAP lock, not the recorded core statistical rules. |
| Config checksum | `BLOCKED` | Phase 1C config keeps all fit/test authorization false. It remains a draft while upstream gates are open. | Validate schema and cross-document invariants after all decisions; record exact SHA-256 in run manifest and preservation manifest. | Executable run authority. |
| Aggregate safety | `PARTIAL` | Phase 1B aggregate follow-up gate passed. That does not prospectively validate new diagnostics or a future run. | Each restricted audit and eventual run must pass the allowlisted aggregate-only export gate; restricted root must resolve outside Git. | Export of any new aggregate packet. |
| Owner authorization | `BLOCKED` | No explicit authorization exists for re-embedding, fitting, regeneration, or test access. | Written authorization referencing source commit, config/SAP checksums, approved embedding path, and passed gate record. | Every confirmatory operation. |

## Locked model-independent decisions

These choices are recorded now without opening access:

- one deterministic study per subject; no repeated-study primary analysis;
- imaging-eligible, target-observed common denominators exactly shared across all modalities;
- the five no-cine studies excluded from every primary modality and optionally retained only in a separately labeled structured full-availability sensitivity;
- all aliases and prohibited dependency/family fields removed before eligibility, imputation, indicators, or scaling;
- train-only feature eligibility, median imputation, missing indicators, and scaling;
- identical Ridge alpha and logistic C grids across modalities, validation-only selection, and no train-plus-validation primary refit;
- continuous LVEF MAE as the primary anchor;
- separately trained logistic regression for primary binary fidelity, with thresholded continuous regression secondary;
- Platt sigmoid calibration fitted on validation after C selection, explicitly secondary/conditional and potentially optimistic because validation is reused;
- validation-only Youden-J operating point with deterministic ties and no deployment-utility claim;
- common-draw paired subject bootstrap and all three modality contrasts;
- one core Holm family across four fusion incremental-value claims (two continuous-LVEF MAE contrasts and two locked strict-panel macro normalized-MAE contrasts), with binary AUROC in a separate explicitly secondary Holm family;
- LVEF tolerance, tie, and band values labeled only as expert inference;
- no accuracy claim for naturally unobserved labels; and
- prespecified, support-gated age/administrative-sex subgroup reporting, with race/ethnicity conditional on a locked source mapping and governance review.

## Binary estimand ruling

The separately trained logistic model is primary for binary `lvef < 40` because it directly estimates the accepted-abstract categorical estimand and yields a discrimination/calibration analysis. The Ridge model remains primary overall for continuous LVEF; thresholding its prediction is a secondary coherence analysis. The exact-40 and `<50` definitions receive separate sensitivity models with the same specification and cannot replace `<40` based on results.

Calibration does not share the status of the primary discrimination estimate. C is selected on validation AUROC, then Platt calibration and the operating cutoff are fit on that same validation cohort. This avoids test leakage but is not independent calibration validation; the limitation and validation event counts must be reported.

## Practical-equivalence language

The 1.0-EF-point paired-MAE margin is expert inference. Interval categories are “margin-exceeding lower error,” “statistically lower error with magnitude unresolved,” “practical equivalence under the expert-inference margin,” “margin-exceeding higher error,” or “indeterminate.” The analysis must not call the margin a guideline clinical-equivalence threshold, MCID, or validated noninferiority margin.

## Future preservation authority

The new post-revalidation manifest must use validated safe relative paths and record, for every preserved file, its size and SHA-256. Run-level metadata must include source commit; command and config checksums; checkpoint checksum; Python, PyTorch, scikit-learn, CUDA, and cuDNN versions; GPU and scheduler/job identity; UTC timestamp; cohort, split, panel, and model versions; and aggregate safety-gate result. The manifest and its checksum are created after an authorized run; they cannot be backfilled from the defective historical pack.

## Authorization rule

Confirmatory access can open only when every `BLOCKED` or `PARTIAL` gate above is replaced by documented `PASS` or an explicitly approved limitation that does not invalidate the estimand, and all of the following are recorded together:

1. source commit;
2. final SAP and config versions/checksums;
3. canonical cohort, split, panel, dependency-registry, model, and embedding versions;
4. checkpoint and environment identity;
5. passing common-denominator, masking, and aggregate safety gates; and
6. explicit owner authorization.

Until then, all authorization booleans remain `false`.
