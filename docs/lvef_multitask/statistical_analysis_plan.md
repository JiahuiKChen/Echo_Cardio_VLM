# Statistical analysis plan: LVEF/multitask revalidation

- SAP draft: `phase1b-draft-v1.1`
- Date: 2026-08-03
- Branch: `codex/lvef-multitask-revalidation`
- Historical base commit: `23c74ccfd145ab9a423b6942a431a1894a34ab67`
- Phase 1A audit commit: `62c982bb9fee602cdb7699a6cbebaab3c9852d1c`
- Status: **not locked; confirmatory fitting and test-performance access are prohibited**

This document is a prospective draft informed only by aggregate, non-performance Phase 1A audits. It preserves the intended analysis while making the remaining lock conditions explicit. It is not a run authorization. A final SAP version/date must be issued after clinical adjudication, artifact/provenance resolution, cohort lock, and approval of clinical margins. Any later amendment must document its reason without using new test performance and must precede the confirmatory run.

## 1. Historical analysis versus confirmatory revalidation

The accepted version-10 abstract and `docs/results_snapshot/2026-04-01_fullscale/` are immutable historical authorities. Historical values will not be overwritten. The Phase 1A aggregate packet is audit evidence, not a new results snapshot and not confirmatory performance.

Separately, the complete `SHA256SUMS` check of the restricted SCC `freeze_fullscale_...` preservation pack currently fails. Selected duplicate pairs are byte-identical, but this does not validate that whole SCC pack. The check was not performed against the Git directory `docs/results_snapshot/2026-04-01_fullscale/`. The new revalidation, if authorized, will use a new dated result set with distinct configs, commands, aggregate outputs, hashes, and claim boundaries.

## 2. Provisional primary question

Among one-study-per-subject MIMIC-IV-ECHO studies with observed quantitative report labels and a usable frozen whole-study video representation, does adding leakage-minimized, realistically masked structured measurements to the representation—or adding the representation to structured measurements—improve held-out quantitative report completion on identical subjects?

The exact primary panel, task families, and clinical equivalence margins are not yet locked. This question becomes confirmatory only after external clinical/formula adjudication is reviewed and incorporated without reference to model performance.

## 3. Cohort and split estimand

- Primary population: the historical 4,530-patient selected cohort, with one deterministic study per patient.
- Imaging-evaluable population: selected studies with a usable study embedding under a prespecified imaging-usability rule. Phase 1A finds 4,525 such studies; the root cause for five exclusions must be resolved before lock.
- Split authority candidate: the restricted 4,530-subject split map (3,171 train, 679 validation, 680 test), after final checksum/provenance confirmation.
- No subject may appear in more than one split.
- The target-specific common cohort is the intersection of observed target, usable study representation, and all prespecified modality prerequisites.
- Vision-only, structured-only, and early fusion must use identical ordered subject-study-target rows and identical target values for training, validation, and test comparisons.
- Equal counts are insufficient; restricted-memory set, pair, ownership, split, and target equality must be checked, with aggregate flags and discrepancy counts exported.
- Repeated-study expansion is excluded from this SAP.

Phase 1A establishes that the historical multitask structured-only tables include up to five studies that vision-only and fusion omit. Those historical cross-modality results are not a valid paired common-denominator analysis. This discrepancy must be corrected by constructing the common cohort before any new fitting, not by post hoc metric adjustment.

## 4. Candidate analysis panels—not yet clinical authorities

### Historical panel

`legacy29` denotes the 29 known-unit/support tasks reported in the accepted abstract. The restricted artifact named `strict_tasks` is this historical support panel; its filename must not be interpreted as a leakage-minimized designation. `legacy29` may be reproduced only as a historical sensitivity and may not be called clinically adjudicated or leakage-minimized.

### Candidate strict panel

The working label `strict21-v1` is a **candidate hypothesis**, not a frozen panel. Its current candidate targets are:

- LV end-diastolic and end-systolic diameters;
- septal and inferolateral wall thickness;
- RV diameter;
- LA four-chamber length, LA dimension, and RA length;
- sinus, ascending-aorta, and arch diameters;
- AV peak velocity;
- MV peak A and one harmonized MV peak E;
- septal and lateral e-prime;
- TR peak velocity;
- LVOT VTI and LVOT diameter;
- TAPSE;
- IVC diameter.

For a target, the complete adjudicated dependency family will be removed from structured predictors. Unadjudicated relationships fail closed. External adjudication must determine duplicates/synonyms, deterministic and near-deterministic derivatives, family membership, clinically near-target fields, units, and whether any candidate target is unsuitable. The final panel may change without consulting model performance.

### Candidate pragmatic panel

The working label `pragmatic26-v1` is also an unfrozen hypothesis: candidate strict targets plus BSA, height, resting heart rate, resting SBP, and resting DBP. Exact aliases and deterministic or near-deterministic derivations remain prohibited. Clinically related nonalgebraic same-report predictors may be allowed only after adjudication and must be labeled pragmatic, not leakage-minimized.

Fractional shortening and TR gradient are candidate deterministic-calculation controls rather than scored targets. The proposed merge of `mv_peak_e` and `mitral_e_velocity` remains an adjudication hypothesis, not an authority.

## 5. Target masking and preprocessing order

For each observed target:

1. Identify the target from the locked raw/canonical mapping.
2. Remove the exact target and all adjudicated raw/canonical aliases.
3. Remove deterministic ancestors/descendants and, for strict analysis, the entire adjudicated target family.
4. Freeze allowed predictor names and the availability-mask definition.
5. Determine feature eligibility from training data only.
6. Fit imputation on training data only.
7. Apply a fixed, prespecified missing-indicator rule and fit scaling on training data only.
8. Fit candidate models on training data only.
9. Select hyperparameters and any operating rule using validation data only.
10. Evaluate the selected specification once on the locked test set.

Target/family removal must occur before imputation, missing-indicator creation, scaling, or any other transform. A masked field may not influence feature eligibility or preprocessing statistics. The exact training-support/availability rule for feature eligibility and the exact rule for creating, retaining, or suppressing missing indicators remain unresolved and must be locked before fitting.

## 6. Predictors and models

- Vision-only: 512-dimensional study mean of frozen EchoPrime video-encoder clip embeddings.
- Structured-only: allowed structured measurements after the applicable mask, training-only feature eligibility, median imputation, prespecified missing indicators, and standardization.
- Early fusion: concatenated vision and allowed structured blocks, using the same target-specific cohort and structured preprocessing path.
- Candidate primary model: Ridge regression.
- Binary LVEF sensitivity: candidate class-weighted logistic regression. The penalty/C grid, compatible solver, class-weight rule, validation selection metric, and tie-break remain unresolved.
- Learned pooling, transformers, end-to-end pixel models, modality gating, and generative models are excluded.

The current `echo_prime_encoder.pt` checkpoint has SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`, but Phase 1A does not prove that it generated the historical embeddings. Checkpoint-to-embedding provenance is a pre-run gate.

Probability calibration is not yet specified. Before fitting, the final SAP must state whether calibration is omitted or uses a prespecified method, which non-test data fit it, and how calibration is kept separate from hyperparameter selection. Likewise, any reported sensitivity/specificity/PPV/NPV requires a validation-only operating-point criterion and tie-break fixed before test access.

## 7. Validation-only model selection

Candidate Ridge alpha grid: `0.001, 0.01, 0.1, 1, 10, 100, 1000`.

For each target and modality, select alpha by minimum validation loss using the target's prespecified primary metric. Ties select the larger alpha. No test metric may influence alpha, feature eligibility, panel membership, masking, threshold, calibration, margin, or exclusion.

The selected train-only fit will be evaluated on test without train-plus-validation refitting in the primary analysis. Any refit analysis must be separately prespecified before test access.

Before this section can be locked, the binary model must have a fixed penalty/C grid, solver compatibility rule, class-weight rule, selection metric, and tie-break. The structured path must have a fixed training-only feature-eligibility threshold and missing-indicator policy. Calibration and operating-point policies must state their validation-only data flow and must not reuse test outcomes.

## 8. Estimands and metrics

### LVEF regression anchor

- Primary candidate metric: MAE in EF percentage points.
- Secondary: RMSE, R-squared, Pearson and Spearman correlation, continuous calibration intercept/slope, and externally adjudicated absolute-tolerance rates.

### Multitask report completion

- Per-task primary candidate metric: MAE divided by the training-target IQR.
- Also report native-unit MAE, RMSE, R-squared, correlation, continuous calibration, and externally adjudicated tolerance rates.
- Candidate panel summary: unweighted mean normalized MAE across every task retained in the final locked strict panel, with all tasks shown.
- Secondary summaries: median/IQR across tasks, family-balanced means, negative-R-squared count, worst-task results, and win/loss/tie counts using predeclared clinical tie margins.

Mean normalized MAE and mean R-squared alone are insufficient because task support and missingness are heterogeneous. Task-level denominators and uncertainty remain visible; no undefined task may be silently removed from a macro summary.

### Binary endpoints

- Historical reduced LVEF remains `<40%` and will not be silently changed.
- Primary candidate discrimination measure: AUROC.
- Secondary: average precision, Brier score, calibration intercept/slope, and performance at validation-selected operating points.
- A probability threshold of 0.5 is not clinically interpreted without calibration.
- LVEF `<50%` is allowed only if external adjudication supports it as an explicitly secondary sensitivity and it is locked before test access.

Clinical absolute-error/equivalence margins and task-specific binary thresholds remain unresolved pending external adjudication. They must not be selected from historical or confirmatory model performance.

## 9. Paired contrasts and uncertainty

For each target and applicable panel summary, calculate:

- early fusion minus vision-only;
- early fusion minus structured-only;
- structured-only minus vision-only.

Use 10,000 paired subject-level bootstrap replicates. Resample held-out subjects with replacement once per replicate and apply the same draw to all modalities and, for panel summaries, all tasks. Report percentile 95% intervals, seed, valid-replicate count, and undefined-metric frequency. The primary cohort has one study per subject; any future repeated-study analysis would carry every eligible study for each sampled subject.

Intervals are conditional on fixed selected models unless a separately labeled refitting bootstrap is prespecified. Statistical superiority must not be claimed from separately generated model-specific intervals. It requires the paired interval for the prespecified contrast to exclude the null in the favorable direction and compliance with multiplicity rules.

## 10. Multiplicity

- The two candidate primary strict-panel contrasts—fusion versus vision and fusion versus structured—use Holm familywise adjustment at two-sided alpha 0.05.
- LVEF is a prespecified clinical anchor reported with paired intervals; avoid a separate binary significance claim unless explicitly placed in the final hierarchy.
- Per-task contrasts are secondary/exploratory. If tests are supplied, report adjusted false-discovery-rate q-values while retaining effect estimates and intervals.
- Clinical win/tie/loss margins must be externally adjudicated and locked before test access.

These rules become binding only when the final panel and estimands are locked.

## 11. Missingness and masking analyses

- Primary evaluation is conditional on the target being observed in the historical report.
- Single-target and whole-family masking are required candidates.
- Random 10%, 30%, and 50% structured-field masking and empirically sampled joint masks are secondary candidates.
- Empirical availability/mask distributions are learned from training reports only.
- Report by structured-information burden and prespecified pattern strata, with suppression of rare patterns.
- Natural missingness is plausibly informative/MNAR. No accuracy claim is permitted for truly missing labels without independent reference remeasurement.
- Missing indicators may encode workflow and provenance; analyses with and without them must be distinguished.

Phase 1A training data show task missingness from 0.47% (BSA) to 75.12% (TAPSE), with IVC diameter, height, fractional shortening, arch diameter, tissue-Doppler e-prime, and LV end-systolic diameter among the more incomplete fields. The training-only pattern distribution may define realistic simulations but cannot establish what an unreported value truly was.

## 12. Calibration and selective prediction

Optional only after the primary revalidation specification is locked:

- validation-calibrated conformal intervals for continuous targets;
- validation-fixed abstention rules;
- risk-coverage curves;
- interval coverage/width by target family and missingness burden.

No threshold, interval, or coverage rule may be tuned on test.

## 13. Pre-fit failure rules

Fail before model fitting if any of the following is unresolved:

- worktree commit/config mismatch with the run manifest;
- unresolved SCC preservation-pack checksum or required input-artifact authority failure;
- unresolved checkpoint-to-embedding provenance or missing new-run environment capture;
- unresolved clip-component non-index payload mismatch;
- unresolved root cause or exclusion rule for the five selected studies without cine candidates;
- split overlap, duplicate assignment, or invalid subject-study ownership;
- nonidentical modality IDs, subject-study pairs, split assignments, or target values;
- target, alias, deterministic derivative, or prohibited family member surviving the mask;
- preprocessing fitted outside training data;
- unresolved binary logistic penalty/C grid, solver, class-weight, selection metric, or tie-break;
- unresolved probability-calibration policy or validation operating-point criterion;
- unresolved structured feature-eligibility or missing-indicator rule;
- unresolved unit, raw/canonical mapping, clinical family, or equivalence margin for an included target;
- target support below the prespecified minimum in train, validation, or test;
- nonfinite inputs after declared preprocessing;
- restricted output resolving inside the Git repository;
- unlogged design change after new test results exist.

Model failures and undefined metrics remain visible in aggregate failure tables and macro-summary denominators.

## 14. Phase 1B lock requirements

Before issuing a final SAP and authorizing confirmatory test access:

1. Classify the complete SCC preservation-pack checksum failure without modifying the pack or the Git historical snapshot.
2. Classify the 9,605 clip-key payload mismatches and establish whether they alter scientific content.
3. Resolve the five-study cine-stage attrition and lock the imaging-usability exclusion/reprocessing rule.
4. Establish the checkpoint-to-embedding link or approve explicit limitation language and a defensible alternative.
5. Capture the exact environment for the future run.
6. Review external clinical/formula adjudication; version the dependency registry and lock raw-name family exclusions.
7. Lock strict/pragmatic panel membership, units, minimum support, families, thresholds, and clinical margins without performance selection.
8. Lock the binary logistic penalty/C grid, solver, class-weight rule, selection metric/tie-break, probability-calibration policy, validation operating-point criterion, structured feature-eligibility rule, and missing-indicator rule.
9. Generate an aggregate common-cohort dry run that passes every exact identity gate for all modalities and targets.
10. Record governance approval for any deterministic regeneration of missing historical predictions; none is currently authorized.

## 15. Aggregate-only export policy

Git may receive aggregate counts, suppression-safe missingness tables, task/family metrics, paired-delta intervals, configuration, environment/checkpoint hashes, and code. Subject/study IDs, labels, predictions, embeddings, DICOM paths, manifests, logs, and restricted discrepancy files remain on SCC.

Every future aggregate snapshot requires SHA-256 checksums, source commit, config checksum, command log, data-release identity, environment metadata, checkpoint checksum, and explicit historical-versus-revalidation labeling.
