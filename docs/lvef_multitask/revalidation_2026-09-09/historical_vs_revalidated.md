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
| LVEF rows | Historical n=2,833, test426 | Reverify exact identities/labels/splits against final store; do not assume from matching counts |
| Imaging absence | Five no-cine studies; historical broader structured denominator sometimes retained them | Exclude five from every primary modality; report full-availability structured sensitivity separately |
| Multitask denominator | Vision/fusion historical 3,166/678/678; structured 3,169/679/679 | Identical ordered target-specific rows across modalities; all-missing structured context does not exclude an otherwise eligible row |
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

## Versioned reporting rule

The final poster/manuscript should label historical and revalidated analyses, show their dates and denominators, and explain changes in representation, leakage controls, panel membership, and inference. Keep historical figures separate rather than connect them to new estimates as if they were paired repeated measurements. If updated-result permission requires accepted numbers to remain visible, use a clearly marked historical inset and updated panel once permitted. If permission is unresolved, prepare both layouts as drafts without selecting a submission version.
