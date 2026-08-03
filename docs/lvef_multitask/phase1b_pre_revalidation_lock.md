# Phase 1B pre-revalidation lock

## Current decision

**The lock is not passed. Confirmatory model fitting, deterministic prediction regeneration, and new test-performance access are not authorized.**

This gate record is based on the aggregate-only Phase 1A packet (SHA-256 `2cdb8dbf01ab37b6ba199db41c8ed45a696b45a6ec32c91189b525bb80ff4c41`) generated from commit `62c982bb9fee602cdb7699a6cbebaab3c9852d1c`. It contains no confirmatory performance. Version 10 of the accepted ASA abstract and the historical LVEF `<40%` endpoint remain unchanged.

## Authority boundaries

| Evidence | Authority | Boundary |
|---|---|---|
| Accepted abstract version 10 | Exact historical wording and reported historical results | Never rewritten by revalidation findings |
| Git historical aggregate snapshot | Immutable files under `docs/results_snapshot/2026-04-01_fullscale/` | Historical numeric authority; the Phase 1A SCC checksum command did not test this Git directory |
| SCC preservation pack | Restricted `freeze_fullscale_...` directory | Complete `SHA256SUMS` validation fails; do not call this separate pack checksum-validated |
| Phase 1A packet | Aggregate audit counts, schemas, hashes, and equality flags | No row-level authority, no performance, no permission to fit |
| Restricted SCC sources | Candidate authorities for identifiers, inputs, processing lineage, and environment | May be inspected only through approved aggregate diagnostics until lock |
| External clinical/formula adjudication | Evidence to inform task families, leakage exclusions, units, thresholds, and margins | Must be reviewed critically; it does not become authority automatically |

## Resolved or sufficiently narrowed items

| Item | Phase 1A conclusion | Lock effect |
|---|---|---|
| Selected-cohort integrity | 4,530 unique subjects and studies; one study per subject; no duplicate or ownership failure | Passed |
| Selected batch partition | 329 selected Stage-D studies plus 4,201 studies in nine batches; exact partition, no duplicate batch assignment | Passed |
| Subject split structure | 3,171 train, 679 validation, 680 test; no duplicate subjects, invalid split values, or cross-split overlap | Passed, pending final artifact provenance |
| Download/readability containment | All 4,530 selected studies reach both stages | Passed at study level |
| Downstream attrition stage | Five selected studies first disappear at cine candidacy; no later losses | Stage resolved; cause and action unresolved |
| LVEF label derivation | 2,836 selected numeric LVEF studies before imaging; 2,833 after embedding; historical manifest agrees on values and `<40%` labels | Passed for derivation/intersection; three-way historical pairing still blocked |
| LVEF vision/structured identity | Exact IDs, subject-study pairs, splits, labels, and binary threshold agree for 2,833 studies | Passed for these two historical tables |
| Historical 29-task definitions | Wide, long, and all three modality tables contain the same 29 target names; no blank/duplicate definitions | Passed as historical `legacy29`, not as a clinical panel |
| Multitask label consistency | Labels, ownership, and splits agree on common rows; no duplicate prediction keys | Passed on the intersection |
| Training missingness | Training-only availability and suppression-safe pattern summaries generated for all 29 tasks | Passed as descriptive input to masking design only |
| Current checkpoint file identity | `echo_prime_encoder.pt`, SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b` | File identified; historical use not proven |

## Do-not-proceed blockers

| Gate | Evidence now | Evidence required to close | Blocks |
|---|---|---|---|
| SCC preservation-pack integrity | Complete `SHA256SUMS` status is `FAIL`; 11 selected duplicate pairs are identical | Safe per-entry checksum status/counts and filenames that classify missing, extra/stale, malformed, invalid-path, or byte-mismatch causes without modifying the SCC pack | Confirmatory input authority and “checksum-validated SCC preservation pack” claim; this does not alter the Git snapshot's historical role |
| Clip component-union provenance | Key sets/multisets, counts, and ownership agree; 9,605 keys have non-index payload mismatches | Aggregate mismatch counts by payload column, component, selected status, and last successful stage; determination of scientific versus normalization-only impact | Use of merged embeddings as fully provenance-validated input |
| Five-study cine attrition | Five are readable but absent from cine candidates; three have LVEF | Aggregate reason classification and a prespecified decision: true imaging-usability exclusion or deterministic reprocessing | Final imaging cohort and denominator lock |
| Multitask common denominator | Structured/panel sources include up to five rows per task beyond vision/fusion; 344 identity checks fail | A pre-fit common-cohort dry run with zero subject, study, pair, split, target, and label identity failures for every locked target | Paired incremental-value analysis |
| Clinical dependency registry | Script-generated registry exists; candidate relationships have not been independently adjudicated | Reviewed evidence-based family/alias/derivative classifications with formulas, units, direction, and support hierarchy | Leakage-minimized masking and final panel |
| Panel/margin lock | `strict21-v1` and `pragmatic26-v1` are working hypotheses; clinical tie/error margins unresolved | Versioned approved panel membership, task families, units, minimum support, thresholds, and margins selected without model performance | Final SAP and confirmatory estimands |
| Binary model specification | Logistic regression is only a model-class candidate; penalty/C grid, compatible solver, class-weight rule, validation selection metric, and tie-break are unset | Versioned fixed values selected without test performance | Reproducible binary fitting and comparison |
| Calibration and operating point | No probability-calibration policy or validation operating-point criterion is locked | Prespecify no calibration or a method/data split; separately prespecify validation-only operating criterion and tie-break | Calibration and threshold-dependent claims |
| Structured preprocessing | Target masking order is fixed, but feature-eligibility support/availability and missing-indicator rules are unset | Fixed training-only eligibility threshold and explicit indicator creation/retention/suppression rule | Leakage-safe, reproducible structured/fusion fitting |
| Historical checkpoint linkage | Current checkpoint hash is known; its use for historical embeddings is not proven | Historical metadata/log/checksum link, or an approved alternative with explicit limitation and any required representation regeneration separately authorized | Precise model provenance and potentially confirmatory embedding use |
| Historical package environment | Two metadata files exist but packet does not establish package/CUDA values | Safe aggregate/key-only inspection sufficient to identify authoritative environment fields; new-run environment capture plan | Exact historical reproducibility claim; new run must capture its own environment |
| LVEF early-fusion predictions | Historical equivalent prediction table was not preserved | Separate authorization for deterministic model-only regeneration, after all upstream locks; common-cohort dry run first | Historical three-way paired LVEF deltas |

## Claim-only limitations if not recoverable

Some historical provenance gaps may ultimately be irrecoverable. They may be downgraded from hard blockers only through a documented governance decision before test access. If so, the manuscript must explicitly distinguish:

- historical accepted results from the new revalidation;
- a currently verified checkpoint file from proof that it generated historical embeddings;
- a newly captured software environment from the unreconstructed historical environment;
- observed-label report completion from truth for naturally missing labels;
- available-case benchmark performance from a paired common-denominator estimand.

No limitation waiver can make a failed checksum pass, infer a clinical dependency, or justify a denominator mismatch.

## Authorized Phase 1B work before lock

The following work is authorized because it does not fit models or inspect new performance:

1. Run safe aggregate-only diagnostics that classify the SCC preservation-pack checksum failure, clip payload mismatches, five-study cine attrition, and environment/checkpoint metadata.
2. Complete external clinical/formula adjudication prompts and critically review the response and cited sources.
3. Version the raw/canonical dependency registry and leakage evidence matrix based on independently supported relationships.
4. Define candidate task families, units, minimum support, binary thresholds, and clinical margins without using model results.
5. Prespecify the binary logistic grid/class weighting, probability-calibration policy, validation operating-point criterion, structured feature-eligibility rule, and missing-indicator rule without using test results.
6. Run aggregate common-denominator **dry runs only**, with no model fitting and no metric calculation.
7. Update documentation, configs, audit code, and synthetic tests.

The following remain unauthorized:

- any confirmatory or exploratory model fitting;
- regeneration of historical predictions;
- calculation or inspection of new test metrics;
- performance-driven panel or endpoint selection;
- modification of the Git historical snapshot, SCC preservation pack, or restricted authority artifacts;
- movement of restricted identifiers, labels, predictions, paths, or embeddings into Git.

## Clinical panel freeze rule

The names `strict21-v1` and `pragmatic26-v1` are provisional labels only. They must not appear in a run manifest as `locked: true` until the external response has been checked against primary guidelines/methodology sources and every included target has:

- an approved canonical definition and unit;
- a resolved alias/duplicate classification;
- deterministic and near-deterministic dependency decisions;
- an approved strict-family exclusion set;
- an approved pragmatic predictor set;
- a prespecified minimum denominator;
- an approved clinical absolute-error/equivalence margin, or an explicit decision that none is defensible.

Unsupported recommendations remain unresolved and fail closed for strict analysis.

## Final authorization checklist

The project may move from `blocked_pending_phase1b_pre_revalidation_lock` to `locked_for_confirmatory_run` only when all boxes are affirmatively documented:

- [ ] SCC preservation-pack checksum defect classified and required input authorities independently verified; the Git snapshot remains separately identified.
- [ ] Clip payload mismatches classified and resolved or shown not to alter scientific content.
- [ ] Five-study imaging-usability/reprocessing rule locked.
- [ ] Historical checkpoint-to-embedding provenance resolved or formally dispositioned.
- [ ] New-run environment and checkpoint capture specified.
- [ ] External clinical/formula adjudication reviewed; citations verified.
- [ ] Dependency registry, strict/pragmatic panels, units, families, support thresholds, and margins versioned.
- [ ] Binary penalty/C grid, solver, class-weight rule, selection metric/tie-break, calibration policy, and validation operating-point criterion versioned.
- [ ] Structured feature-eligibility and missing-indicator rules versioned as training-only preprocessing.
- [ ] Target-family mask audit passes with fail-closed treatment of unresolved relationships.
- [ ] Pre-fit common-denominator dry run passes for every modality, target, and split.
- [ ] Final SAP/config checksums recorded and no design change follows test access.
- [ ] Restricted output root confirmed outside Git and aggregate-only export gate passes.
- [ ] Explicit owner authorization for the confirmatory run recorded.

Until then, test access remains closed.
