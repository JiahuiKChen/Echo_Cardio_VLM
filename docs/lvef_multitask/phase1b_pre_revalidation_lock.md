# Phase 1B pre-revalidation lock

## Current decision

**The lock is not passed. Confirmatory model fitting, deterministic prediction regeneration, embedding regeneration, and new test-performance access are not authorized.**

This gate record began with the aggregate-only Phase 1A packet (SHA-256 `2cdb8dbf01ab37b6ba199db41c8ed45a696b45a6ec32c91189b525bb80ff4c41`) generated from commit `62c982bb9fee602cdb7699a6cbebaab3c9852d1c`, and now records the Phase 1B follow-up dispositions adopted during Phase 1C. It contains no confirmatory performance. Version 10 of the accepted ASA abstract and the historical LVEF `<40%` endpoint remain unchanged. The current controlling gate is `phase1c_pre_revalidation_lock.md`; this document preserves the preceding gate lineage without contradicting that lock.

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
| Downstream attrition stage | Five selected studies first disappear at cine candidacy; no later losses | Stage and reason resolved as `NO_MULTIFRAME_CINE_CANDIDATE`; provisional imaging-ineligibility/common-exclusion rule recorded, with final authority identity still pending |
| LVEF label derivation | 2,836 selected numeric LVEF studies before imaging; 2,833 after embedding; historical manifest agrees on values and `<40%` labels | Passed for derivation/intersection; three-way historical pairing still blocked |
| LVEF vision/structured identity | Exact IDs, subject-study pairs, splits, labels, and binary threshold agree for 2,833 studies | Passed for these two historical tables |
| Historical 29-task definitions | Wide, long, and all three modality tables contain the same 29 target names; no blank/duplicate definitions | Passed as historical `legacy29`, not as a clinical panel |
| Multitask label consistency | Labels, ownership, and splits agree on common rows; no duplicate prediction keys | Passed on the intersection |
| Training missingness | Training-only availability and suppression-safe pattern summaries generated for all 29 tasks | Passed as descriptive input to masking design only |
| Current checkpoint file identity | `echo_prime_encoder.pt`, SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b` | File identified; historical use not proven |
| Clip payload reconciliation | All 9,605 original non-index payload mismatches have zero residual full-row tuple mismatches after normalization; differences are `embedding_l2_norm` serialization plus expected index rewriting | Passed; no longer a scientific blocker |
| Five-study attrition reason | All five stop at `NO_MULTIFRAME_CINE_CANDIDATE`; three train, one validation, one test | Provisional imaging-ineligibility rule resolved; common-denominator proof still required |
| Preservation-pack defect class | All 14 checksum lines use invalid relative paths; 13 files are unlisted; no valid expected-file entry reached byte comparison | Historical pack permanently unsuitable as future preservation authority; use a new manifest |

## Do-not-proceed blockers

| Gate | Evidence now | Evidence required to close | Blocks |
|---|---|---|---|
| Historical preservation-pack disposition | Defect classified: 14 invalid manifest-relative paths and 13 unlisted files. Zero byte mismatches is non-evidence because no expected entry passed path validation | No repair is permitted. Independently hash every future input and create a new complete post-revalidation preservation manifest | “Historical SCC pack checksum-validated” claim; does not by itself block a newly preserved run |
| Duplicate clip keys | The 9,605 serialization/index differences are reconciled, but 32 selected-cohort keys are duplicated in both `batch_000` and the merged manifest | Restricted content/vector diagnostic; resolution by deterministic deduplication, affected reprocessing, or clean selected-only re-embedding; fresh clip/study checksums | Historical merged/study embedding authority and all confirmatory vision/fusion access |
| Selected canonical clip authority | Historical store contains 4,525 selected plus 171 outside-selected studies and the unresolved duplicated keys | One canonical row per physical imaging-eligible selected-cohort clip, zero nonselected studies, validated source/extracted mapping, and fresh checksums | New selected-cohort embedding authority |
| Five-study imaging eligibility | All five are readable but have `NO_MULTIFRAME_CINE_CANDIDATE`; all have `legacy29` labels and three have LVEF | Primary common-cohort construction must exclude all five from every modality and pass exact identity gates; any structured full-availability sensitivity is separately labeled and unpaired | Exact primary common denominator, not the provisional eligibility rule itself |
| Multitask common denominator | Structured/panel sources include up to five rows per task beyond vision/fusion; 344 identity checks fail | A pre-fit common-cohort dry run with zero subject, study, pair, split, target, and label identity failures for every locked target | Paired incremental-value analysis |
| Clinical dependency registry | Script-generated registry exists; candidate relationships have not been independently adjudicated | Reviewed evidence-based family/alias/derivative classifications with formulas, units, direction, and support hierarchy | Leakage-minimized masking and final panel |
| Panel/margin lock | Three distinct constructs are under review: strict leakage-minimized measurement, target-family-masked report completion, and pragmatic same-report completion. Membership and non-LVEF native-unit margins remain provisional | Versioned evidence-based disposition for every target, separate formula sets from correlation families, verified project units/definitions, and approved thresholds/margins selected without model performance. BSA, height, blood pressure, and heart rate are context/metadata rather than primary echo-measurement macro targets | Final SAP and confirmatory estimands |
| Binary model specification | Phase 1C records a fixed L2/liblinear logistic C grid, training-label class weights, validation-AUROC selection, deterministic tie-break, and a separate thresholded-regression secondary analysis | Reconcile with the final panel/registry, validate implementation, and preserve the final SAP/config checksum before authorization | No longer a specification gap; fitting remains blocked by upstream gates and owner authorization |
| Calibration and operating point | Phase 1C records secondary Platt calibration on validation after C selection, its validation-reuse limitation, and a validation-only Youden-J operating point with deterministic ties | Reconcile with the final endpoint registry, validate implementation, and preserve the final SAP/config checksum | No longer a specification gap; calibration and threshold-dependent execution remain unauthorized |
| Structured preprocessing | Phase 1C records target/family removal before transforms, train-only eligibility, median imputation, missing indicators, scaling, and identical structured/fusion transformations | Reconcile with the final dependency masks, validate implementation, and preserve ordered feature-list/config checksums | No longer a specification gap; fitting remains blocked by unresolved clinical and provenance gates |
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

1. Run the restricted, aggregate-gated 32-key diagnostic; do not copy its restricted tables or stderr into Git.
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

No legacy numeric panel label is a locked scientific construct. The strict leakage-minimized measurement panel, target-family-masked report-completion panel, and pragmatic same-report completion panel must be versioned separately. None may appear in a run manifest as `locked: true` until the external response has been checked against primary guidelines/methodology sources, restricted raw metadata has been reviewed, and every included target has:

- an approved canonical definition and unit;
- a resolved alias/duplicate classification;
- deterministic and near-deterministic dependency decisions;
- an approved strict-family exclusion set;
- an approved pragmatic predictor set;
- a prespecified minimum denominator;
- an approved clinical absolute-error/equivalence margin, or an explicit decision that none is defensible.

`body_surface_area`, `height_cm`, `resting_sbp`, `resting_dbp`, and `resting_hr` must not enter the primary echo-measurement macro panel. They may be retained only as explicitly labeled context inputs or a separate metadata benchmark after clinical review. `lvef` remains a separate anchor rather than a multitask macro member.

Unsupported recommendations remain unresolved and fail closed for strict analysis.

## Final authorization checklist

The project may move from `blocked_pending_phase1c_pre_revalidation_lock` to `locked_for_confirmatory_run` only when all boxes are affirmatively documented in the controlling Phase 1C gate:

- [x] SCC preservation-pack defect classified and old pack dispositioned as immutable/non-authoritative; the Git snapshot remains separately identified.
- [x] The 9,605 clip payload differences are reconciled as serialization/index transforms.
- [ ] All 32 duplicated clip keys classified and resolved in a freshly checksum-validated canonical store.
- [x] Five-study provisional imaging-eligibility/denominator rule locked: exclude from all primary modalities; structured full-availability sensitivity is separate and unpaired. Application to the final authorities and exact common-ID proof remain pending.
- [ ] Historical checkpoint-to-embedding provenance resolved or formally dispositioned.
- [x] New-run environment and checkpoint capture fields specified; actual future run capture remains pending.
- [x] OpenEvidence clinical/formula responses critically reviewed and supplied/official citations verified where available; restricted project-metadata adjudication remains pending under the next item.
- [ ] Dependency registry, strict/pragmatic panels, units, families, support thresholds, and margins versioned.
- [x] Binary penalty/C grid, solver, class-weight rule, selection metric/tie-break, calibration policy, and validation operating-point criterion versioned without test performance.
- [x] Structured feature-eligibility and missing-indicator rules versioned as training-only preprocessing.
- [ ] Target-family mask audit passes with fail-closed treatment of unresolved relationships.
- [ ] Pre-fit common-denominator dry run passes for every modality, target, and split.
- [ ] Final SAP/config checksums recorded and no design change follows test access.
- [ ] Restricted output root confirmed outside Git and aggregate-only export gate passes.
- [ ] Explicit owner authorization for the confirmatory run recorded.

Until then, test access remains closed.
