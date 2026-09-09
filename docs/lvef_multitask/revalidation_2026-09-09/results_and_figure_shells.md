# Results and figure shells

Status: **verified input flow with unpopulated statistical templates**. `PENDING` means unavailable, never zero. Reconstruction facts come from the completed C3 handoff; current label denominators come from the completed SCC input and identity replay. Model estimates still require the released validated analysis bundle. Do not import historical predictions or estimates into the revalidated column. See the [current input funnel](current_input_funnel.md) and [readiness record](../analysis_readiness_2026_09_09.md).

## Table 1 / Figure 1: cohort and denominator flow

| Stage | Studies / subjects | Status and interpretation |
|---|---:|---|
| Public source release | 7,243 / 4,579 | Historical provenance reference; not a new C3 count |
| Measurement-linked and ≥5 DICOM objects | 7,104 studies | Historical selection funnel |
| Deterministic one-study-per-subject selection | 4,530 / 4,530 | Completed C3 selection authority |
| Selected split | 3,171 / 679 / 680 | Original train/validation/test map and current subject-study ownership verified |
| Reconstructed imaging representations | 4,525 study vectors; 184,570 clip vectors | Completed C3; selected cohort only |
| Prespecified no-cine exclusion | 5 studies | Exclude in all three primary modalities; no new no-cine attrition |
| Common imaging split | 3,168 / 678 / 679 | Current serialized-input identity audit passed; 4,525 overall |
| Observed selected LVEF before imaging eligibility | 2,836 | 1,998 / 411 / 427; separate exact-name label authority |
| Common observed LVEF | 2,833 | Current exact membership/label replay passed; 1,997 / 410 / 426 |
| Exact common LVEF =40 | 103 | Current label-only boundary audit passed; 71 / 12 / 20 |
| Candidate target-specific common rows | 21 candidates plus LVEF | All 22 targets across three splits replayed; computational support and positive training-IQR checks passed |
| Locked strict-panel target-specific common rows | PENDING final membership | Eight clinical decisions and evidence-based grouped panel/mask/aggregation approval remain; candidate support is not panel authority |

Render flow boxes for selection → imaging eligibility → target observation. Branch the five no-cine cases to a separately labeled structured-only sensitivity. Do not use the older all-study totals of 4,696 embeddings or 191,993 clips as selected C3 totals. Missing target labels are target-specific, not a single universal exclusion count. Source: [denominator funnel](../denominator_funnel.md).

The completed preparation ran at analysis commit `073d3883fc54c4041a043efce850ecfeca07890a`. Input receipt SHA-256: `b82fe7d4a7c3f3cb8aed57038af409d7861aa1b09130292052f0c330cebae4de`; safe aggregate identity/funnel SHA-256: `501501b91069ff9252f0bfc98d102e1d61b18a493f91daa278aa6cd80b99ec01`. The three serialized split arrays were rehashed. Candidate selected numeric counts equaled valid-unit counts, with zero repeated numeric target rows after selection and zero incompatible numeric candidate21 rows. The separate exact-name LVEF analytical scale does not establish a verified native-unit declaration. No model fit, prediction or performance comparison is represented by this table.

## Table 2 / Figure 2: continuous LVEF and paired effects

| Model | Train / validation / test n | MAE, EF points (95% CI) | RMSE | R² | Bias | Error ≤5 EF points |
|---|---|---|---|---|---|---|
| Vision-only | 1,997 / 410 / 426 | PENDING | PENDING | PENDING | PENDING | PENDING |
| Structured-only | 1,997 / 410 / 426; same ordered inputs | PENDING | PENDING | PENDING | PENDING | PENDING |
| Early fusion | 1,997 / 410 / 426; same ordered inputs | PENDING | PENDING | PENDING | PENDING | PENDING |

These are verified prepared-input denominators. They do not imply that models have been fit or evaluated.

| Paired MAE contrast | Difference (95% CI), EF points | Raw p | Core-family Holm p | Valid / 10,000 draws | Interpretation |
|---|---|---|---|---|---|
| Fusion − vision | PENDING | PENDING | PENDING | PENDING | PENDING |
| Fusion − structured | PENDING | PENDING | PENDING | PENDING | PENDING |
| Structured − vision | PENDING | PENDING | Secondary | PENDING | PENDING |

Figure: left panel model MAE points and intervals; right panel paired MAE differences with a zero reference and lightly labeled ±1 EF-point research-margin guides. Negative differences favor the first-named model. Do not infer paired superiority from separate model intervals. Report 0.5/2-point margin sensitivities in an adjacent table. Never label these guides as established clinical benefit or MCID.

## Table 3: complete task and family reporting

Generate one row for **every** locked target, in registry order, with columns:

`construct; family; exact_target; unit; train_n; validation_n; test_n; training_IQR; modality; native_MAE; native_MAE_CI_low/high; MAE_per_train_IQR; RMSE; R2; signed_error; calibration_intercept/slope; valid_bootstrap_draws; margin_status`.

Generate two fusion-contrast rows per target with:

`native_MAE_difference; paired_CI_low/high; raw_p; optional_BH_p_with_declared_family; normalized_MAE_difference; independent_margin_value_or_unresolved; margin_classification_or_not_assessed`.

| Full-panel summary | Target count | Vision | Structured | Fusion | Fusion − vision (paired CI) | Fusion − structured (paired CI) |
|---|---:|---|---|---|---|---|
| Strict mean MAE / training IQR | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| Family-masked, same scored targets | Same locked count | PENDING | PENDING | PENDING | PENDING | PENDING |
| Pragmatic, same scored targets | Same locked count | PENDING | PENDING | PENDING | PENDING | PENDING |
| Secondary family-balanced mean | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |

Figure 3: a complete target forest plot grouped by reviewed family, with native-unit labels/denominators visible; an aligned normalized-error panel enables scale comparison. Include worse/indeterminate tasks and negative R² counts. A top-gains chart or macro-only plot cannot stand in for the complete display. No non-LVEF win/tie/loss labels without independent margins.

## Table 4: binary LVEF and validation-frozen calibration

One row per modality and endpoint (`<40` primary; `<=40` and `<50` sensitivity): train/validation/test n, events/nonevents, AUROC with CI, AP, calibrated Brier, calibration intercept/slope, operating cutoff, sensitivity/specificity/PPV/NPV/F1/balanced accuracy, and confusion counts. Add paired AUROC contrasts and the separate two-claim secondary Holm result for `<40` only. Report unsupported sensitivities explicitly. Keep continuous-model thresholding in a separate coherence table.

## Table 5 / Figure 4: masks and missingness

Diagram contract: target and prohibited aliases/dependencies/family fields → removal from schema → allowed training features → train-only imputation/scaling/indicators → shared transformations → modality-specific training → validation selection → frozen test. Show no route from a removed field into a missingness indicator. Use schematic dependency examples (LV diameters/FS, TR velocity/pressure, duplicate mitral E) marked **registry-dependent**; the diagram does not adjudicate their project-specific identity.

Sensitivity table: with/without indicators on exact common rows; prespecified random 10/30/50% withholding; training joint-pattern withholding; structured full-availability cohort; threshold definitions/bands; eligible demographics with suppression; input-content sensitivities only if independently locked. Include support, paired sample, feature/mask hash, estimate/CI, and exploratory status. Do not combine different denominators into one paired bar chart.

## Result prose awaiting validated values

“The locked `[K]`-target panel included `[F]` reviewed measurement families. All primary modality comparisons used identical ordered rows and target values. In `[N_LVEF_TEST]` test subjects with observed LVEF, MAE was `[V]`, `[S]`, and `[FUS]` EF points. Fusion minus vision was `[D_FV, 95% CI]`, and fusion minus structured was `[D_FS, 95% CI]`. The corresponding strict-panel mean normalized-error differences were `[D_PANEL_FV]` and `[D_PANEL_FS]`. The four-claim Holm analysis showed `[VALIDATED_FAMILY_RESULT]`. `[COMPLETE_TASK_PATTERN]` describes both favorable and unfavorable task estimates.”

If the full core family is not available, state which estimate is descriptive or not released. Do not replace it with an undeclared LVEF-only confirmatory family. The figure renderer must reject missing columns, changed panel order, unequal modality row hashes, missing tasks, or nonfinite required estimates; it must not fill gaps with zeros or historical values.
