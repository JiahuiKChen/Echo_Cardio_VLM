# Verified analysis input preparation — 2026-09-09

**Model-independent preparation passed on SCC; clinical panel and analysis lock
remain pending. No model fitting or test-performance access occurred.** This
record summarizes the closed aggregate projection of the private input receipt;
it contains no subject/study lists, individual labels, predictions or embeddings.

The analysis implementation is `073d3883fc54c4041a043efce850ecfeca07890a`,
executed from a separate detached SCC checkout. The original completed C3
checkout remains at `cda842d04cc18eb3669ad5377c31a6e955fc43cb`.
All four canonical artifact hashes/sizes, study-vector hashes, clip membership,
study ownership and original split membership passed. The exact five-study
no-cine set matches the completed preservation authority. The three serialized
split files were rehashed and all 22 target/anchor × three split ordered
subject-study-label fingerprints were recomputed successfully. These shared
arrays supply identical target rows and labels to the three modalities; the
engine also rejects any later modality mismatch before fitting.

## Cohort and LVEF funnel

| Stage | Total | Train | Validation | Test |
|---|---:|---:|---:|---:|
| Selected one-study-per-subject cohort | 4,530 | 3,171 | 679 | 680 |
| Prespecified no-cine exclusions from every primary modality | 5 | 3 | 1 | 1 |
| Final imaging-eligible cohort | 4,525 | 3,168 | 678 | 679 |
| Exact-name numeric LVEF before imaging intersection | 2,836 | 1,998 | 411 | 427 |
| Exact common LVEF denominator | 2,833 | 1,997 | 410 | 426 |
| Common LVEF exactly 40 | 103 | 71 | 12 | 20 |

Three of the prespecified no-cine studies have an observed exact-name LVEF label
and are excluded at the imaging intersection. The 1,694 selected subjects without
a usable exact-name numeric LVEF remain outside this label denominator. These are label
availability and imaging dispositions, not failures of imputation or prediction.
A row with all finally permitted structured predictors missing remains eligible;
the primary denominator never requires an observed predictor. Any future
structured full-availability sensitivity must retain a separate denominator.

The label authority remains the exact case-sensitive raw `lvef` field, joined by
selected subject/report and checked for study ownership. Numeric repeated rows
would be combined by the within-report median. In this selected export, each of
the 2,836 numeric LVEF source rows represents a distinct selected study, so no
repeated numeric LVEF row is collapsed. The operational scale is EF percentage
points; native source-unit metadata are unverified. The exact label's measurement
method is unspecified and cannot be stratified from the retained export. Actual
method mixture is unknown. No canonical-map alias was added or promoted.

| Binary definition | Train events / nonevents | Validation events / nonevents | Test events / nonevents |
|---|---:|---:|---:|
| `<40`, primary | 189 / 1,808 | 40 / 370 | 34 / 392 |
| `<=40`, sensitivity | 260 / 1,737 | 52 / 358 | 54 / 372 |
| `<50`, sensitivity | 350 / 1,647 | 72 / 338 | 78 / 348 |

All three endpoints meet the prespecified 20-event and 20-nonevent floor in every
split. This label-only check does not select thresholds using performance.

## Candidate target funnel

The following 21 targets remain **candidates**, with LVEF shown separately as an
anchor. Every row passes the 120/40/40/250 support floors and finite positive
training-IQR check. This numerical support does not settle clinical definitions,
alias aggregation, dependency masks, or final scored-panel membership.

For each candidate below, observed numeric selected studies equal numerically
unit-compatible selected studies. Zero candidate numeric source rows were
excluded for incompatible units, and no repeated numeric candidate rows required
median collapse in this selected export. Length/VTI values use explicit cm-to-mm
conversion; the six velocity targets use explicit m/s-to-cm/s conversion.
`mitral_e_velocity`, whose source unit is ms, is outside this table and cannot be
merged into a velocity label under the current source authority. Broader
uncertain-unit predictors remain excluded independently of these candidate
counts. Clinical validity of each retained source aggregation remains pending.

| Target | Unit | Observed / compatible before imaging | Common train | Common validation | Common test | Common total |
|---|---|---:|---:|---:|---:|---:|
| LVEF, separate anchor | EF percentage points | 2,836 | 1,997 | 410 | 426 | 2,833 |
| Aortic arch diameter | mm | 3,253 | 2,263 | 497 | 492 | 3,252 |
| Ascending aorta diameter | mm | 4,019 | 2,804 | 616 | 594 | 4,014 |
| Aortic peak velocity | cm/s | 4,212 | 2,953 | 640 | 614 | 4,207 |
| Inferolateral thickness | mm | 4,352 | 3,042 | 649 | 656 | 4,347 |
| IVC diameter | mm | 1,812 | 1,262 | 280 | 268 | 1,810 |
| LA four-chamber length | mm | 4,167 | 2,908 | 630 | 624 | 4,162 |
| LA dimension | mm | 4,307 | 3,010 | 647 | 645 | 4,302 |
| Lateral e-prime | cm/s | 3,427 | 2,384 | 533 | 506 | 3,423 |
| LV end-diastolic diameter | mm | 4,362 | 3,049 | 651 | 657 | 4,357 |
| LV end-systolic diameter | mm | 3,566 | 2,492 | 530 | 541 | 3,563 |
| LVOT diameter | mm | 3,970 | 2,786 | 589 | 590 | 3,965 |
| LVOT VTI | mm | 3,787 | 2,654 | 573 | 555 | 3,782 |
| Mitral peak A | cm/s | 3,770 | 2,641 | 562 | 564 | 3,767 |
| Mitral peak E | cm/s | 4,278 | 2,995 | 646 | 632 | 4,273 |
| RA length | mm | 4,149 | 2,903 | 623 | 618 | 4,144 |
| RV diameter | mm | 3,899 | 2,723 | 587 | 585 | 3,895 |
| Septal e-prime | cm/s | 3,402 | 2,370 | 525 | 503 | 3,398 |
| Septal thickness | mm | 4,354 | 3,043 | 649 | 657 | 4,349 |
| Sinus diameter | mm | 4,293 | 3,004 | 645 | 639 | 4,288 |
| TAPSE | mm | 1,134 | 787 | 184 | 160 | 1,131 |
| Tricuspid regurgitant peak velocity | cm/s | 3,706 | 2,594 | 560 | 547 | 3,701 |

Differences between each pre-imaging and common total arise from the verified
no-cine intersection. They are not target-specific predictive exclusions.
The accepted historical 29-task panel is unchanged and is not substituted for
the eventual clinically adjudicated strict panel.

## Preparation evidence

| Published record | SHA-256 |
|---|---|
| Owner authorization, conditional on genuine analysis lock | `eadccf524c778206b53ed28fce7f71e04de03ffd677d28a76e1e9b1faef3c650` |
| Analysis environment | `0315a5ab66e8825c7c0a5743bc975af3d520be5dc8d4137dd010b10a82e57bfc` |
| Analysis source bindings | `fc9beea169465d84974cd0589bf0db83dcbe52d90b80c05d7935ada4e1848bf3` |
| Private model-independent inputs | `b82fe7d4a7c3f3cb8aed57038af409d7861aa1b09130292052f0c330cebae4de` |
| Aggregate serialized-identity and funnel replay | `501501b91069ff9252f0bfc98d102e1d61b18a493f91daa278aa6cd80b99ec01` |
| Nine technical dispositions | `427fd59fe75b4985cb2556e94a6b949af1edeabe98282bac0e0eab1c2d28a975` |
| Pending grouped panel/dependency draft | `4d72afcdfa9f6e707e712183c36c1ac6d7a486e4e2cc741b12c30821fb41c0dc` |
| SCC focused synthetic validation, 86 passed | `dcb15fbfcd59eda92e003485228f9dc97a9b0655aee3af41770b2434f7cb9035` |
| Final independent-preparation handoff | `5f09a6b3f17ca1d87807be0e6d6ca1fda56cd4791d53c206b07ae56b661d507d` |

The maintained aggregate validator accepted the closed funnel projection. The
input adapter and review constructor both returned zero. SCC ran all six new
focused test files with isolated pytest 8.3.5 and the unchanged scientific Python
environment. All 86 tests passed. The local maintained suite previously passed
2,063 tests with one skip. These are actual software/input checks, with zero
fitting and zero test-performance access. The preparation helper has finished;
no analysis scheduler job, analysis lock, model freeze or test release exists.

Eight clinician choices remain blank in the existing hash-bound questionnaire.
The final handoff rechecked the actual response, current source and environment
and reports `PASS_INDEPENDENT_PREPARATION_CLINICAL_REVIEW_PENDING`.
After genuine responses, the authorized next work is to apply their consequences,
complete the grouped source-aggregation and positive-predictor review, lock the
panel and specification, then fit/select, freeze and evaluate once. Numerical
support alone cannot stand in for that review. See the
[readiness record](../analysis_readiness_2026_09_09.md) and
[execution runbook](execution_runbook.md).
