# Historical accepted results versus the revalidation

The accepted version-10 text and `docs/results_snapshot/2026-04-01_fullscale/` remain immutable. The comparison below explains why new estimates may differ; it does not claim that the revised procedures have already produced better estimates.

## Historical LVEF estimates — transcription only

| Accepted model | AUC | R² | MAE, EF points |
|---|---:|---:|---:|
| Vision-only | 0.9581 | 0.6127 | 5.6761 |
| Structured-only | 0.9153 | 0.4383 | 6.6842 |
| Fusion | 0.9618 | 0.6615 | 5.3935 |

These values are from [accepted version 10](../accepted_abstract_version10_verbatim.txt), n=426 historical LVEF test subjects. Its fusion-minus-vision MAE difference is −0.2826 EF points. These historical numbers are not new performance access. The preserved historical fusion predictions were not available for a full three-way paired reanalysis, so reported separate-model intervals do not establish a paired superiority claim. Do not manufacture a historical paired confidence interval from them.

## Design comparison

| Dimension | Accepted/historical authority | Revalidated specification and required evidence |
|---|---|---|
| Representation | Historical encoder-only 512-dimensional features and historical pooling lineage | Completed C3 selected-only authority, deterministic stable-order pooling, 184,570 clips / 4,525 study vectors; checkpoint/source/index proofs |
| Cohort | 4,530 selected; historical all-study artifacts also contained 171 outside-selected studies | Exactly selected one-study-per-subject roster; outside-selected representations never enter analysis |
| LVEF rows | Historical n=2,833, test n=426 | Current serialized-input replay passed: common n=2,833, split 1,997/410/426; observed pre-imaging n=2,836, split 1,998/411/427. Current bindings were verified rather than inferred from matching historical counts |
| Imaging absence | Five no-cine studies; historical broader structured denominator sometimes retained them | Exclude five from every primary modality; report full-availability structured sensitivity separately |
| Multitask denominator | Vision/fusion historical 3,166/678/678; structured 3,169/679/679 | Current imaging eligibility 3,168/678/679; fingerprints for all 22 prepared targets across three splits replayed from serialized inputs. Final scored membership awaits clinical/panel lock; all-missing structured context does not exclude an otherwise eligible row |
| Panel | Historical29 selected on numeric support and preferred units | Independently reviewed strict measurement panel; membership unresolved until lock; LVEF separate; context fields outside macro |
| Predictor masking | Accepted text reports four explicit LVEF leakage features removed and51 retained predictors | Target, aliases, duplicates, formulas, method dependencies, and applicable families removed before any eligibility/transform/indicator step; count from final bundle |
| Transformation | Historical implementation retained as historical evidence | Target-specific train-only feature support, imputation/scaling/IQR; shared transformations; explicit no-indicator sensitivity |
| Fitting | Accepted estimates retained without retroactive relabeling | Fixed Ridge/logistic grids, validation-only selection, declared ties/seed/solver; primary training-only model without train+validation refit |
| Binary | `<40`; accepted table says AUC | Separate logistic `<40` primary binary; `<=40` and `<50` secondary; boundary audit; validation-frozen calibration/operating point |
| Panel summary | Historical mean R² / mean MAE-IQR: vision 0.3196/0.4953, structured 0.4385/0.4191, fusion 0.4886/0.3973 | Complete locked-task native errors plus unweighted mean MAE/training-IQR; comparable only with differences in panel and rows explicitly stated |
| Inference | Individual-model AUC bootstrap intervals in accepted text | Paired subject bootstrap, shared multiplicities across tasks/modalities, undefined macro draws reported, full four-claim core Holm family |
| Claim strength | Accepted text describes fusion as strongest overall | Let paired effects and full multiplicity determine claims; report unfavorable tasks and uncertainty; no assumed improvement |
| Validation status | Historical split and test results exposed | Prespecified revalidation on the same historical split; external validation remains future work |
| Generation and clinical impact | Accepted future-facing conditional-generation statement | No generative model, direct image measurement, naturally missing truth, or clinical workflow benefit evaluated |

The [current input funnel](current_input_funnel.md) records the verified model-independent comparison. Exact-40 LVEF counts remain 103 overall and 71/12/20 across train/validation/test. All 21 candidate targets plus LVEF passed computational support/IQR checks; this does not promote candidate21 to the final clinical panel. The nine technical dispositions are published, while eight clinical decisions, final raw aggregation approval and grouped predictor/panel review remain open in the [readiness record](../analysis_readiness_2026_09_09.md).

Preparation used separate analysis commit `073d3883fc54c4041a043efce850ecfeca07890a`; the original C3 checkout and historical snapshots remain preserved. Current input receipt SHA-256 is `b82fe7d4a7c3f3cb8aed57038af409d7861aa1b09130292052f0c330cebae4de`, and the aggregate identity/funnel projection SHA-256 is `501501b91069ff9252f0bfc98d102e1d61b18a493f91daa278aa6cd80b99ec01`. These prove the current input audit, not equivalence of every historical row/value or a complete historical clip-by-clip disposition. Do not attribute the entire historical-to-C3 clip-count difference to one exclusion solely by subtraction. No revalidated performance estimates have been produced.

## Versioned reporting rule

The final poster/manuscript should label historical and revalidated analyses, show their dates and denominators, and explain changes in representation, leakage controls, panel membership, and inference. Keep historical figures separate rather than connect them to new estimates as if they were paired repeated measurements. If updated-result permission requires accepted numbers to remain visible, use a clearly marked historical inset and updated panel once permitted. If permission is unresolved, prepare both layouts as drafts without selecting a submission version.
