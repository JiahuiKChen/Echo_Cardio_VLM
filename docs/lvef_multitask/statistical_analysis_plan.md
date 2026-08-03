# Statistical analysis plan: LVEF/multitask revalidation

- SAP version: `phase1c-prefit-v1.0`
- Date: 2026-08-03
- Branch: `codex/lvef-multitask-revalidation`
- Historical base commit: `23c74ccfd145ab9a423b6942a431a1894a34ab67`
- Phase 1C starting commit: `ccc6b54e5a9138a23ac2cd756e5483217abc11b3`
- Status: **model-independent specifications recorded; final SAP lock and all confirmatory access remain closed**

This document prespecifies the statistical design without using new confirmatory performance. It is not permission to fit a model, regenerate a prediction, or inspect test metrics. The final run manifest may cite this version only after the provenance, duplicate-key, clinical metadata, dependency-registry, panel, common-denominator, config-checksum, safety, and owner-authorization gates in `phase1c_pre_revalidation_lock.md` have passed. A later change must be versioned with a reason that does not depend on test performance and must precede test access.

## 1. Authority and question

The accepted version-10 ASA abstract and `docs/results_snapshot/2026-04-01_fullscale/` remain immutable historical authorities. The historical binary endpoint is exact `lvef < 40`; it will not be rewritten to match a guideline inequality. A new revalidation will receive a distinct dated result set and preservation manifest.

The primary scientific anchor is continuous LVEF report-label agreement. The broader question is whether frozen whole-study imaging representations, leakage-minimized observed structured context, or their early fusion improve quantitative structured report-label completion on identical held-out subjects. This is agreement with observed report labels, not direct caliper/trace localization, physiological ground truth, or accuracy for naturally unreported labels.

## 2. Estimand, cohort, and split

- Primary estimand: performance among the deterministic one-study-per-subject selected cohort that is imaging-eligible and has the target label observed.
- Selected cohort authority candidate: 4,530 studies from 4,530 subjects.
- Split-map authority candidate: the restricted subject split map, with 3,171 train, 679 validation, and 680 test subjects. Its checksum and ownership gates must pass before use.
- The five readable-DICOM studies with reason `NO_MULTIFRAME_CINE_CANDIDATE` are provisionally imaging-ineligible. They are excluded from **all three modalities** in every primary paired comparison.
- A structured-only full-availability sensitivity may retain those five studies. It is a different available-case estimand, must report its own denominators, and cannot support a paired modality or incremental-value claim.
- Repeated studies are excluded. A future repeated-study analysis requires a separate subject-clustered SAP.

For every target and split, the primary common denominator is the intersection of:

1. the selected one-study-per-subject cohort;
2. the locked imaging-eligibility rule;
3. the deterministic split assignment;
4. an observed, valid target after locked raw/canonical and unit handling; and
5. every prespecified modality prerequisite.

Vision-only, structured-only, and fusion use the same ordered subject-study-target rows and exactly equal target values in train, validation, and test. Construction occurs before fitting. Equal counts are insufficient: restricted checks must establish exact equality of subject IDs, study IDs, subject-study pairs, split labels, target names, target values, and row multiplicities, then export only aggregate flags, counts, and checksums. A discrepancy blocks all three modalities for that target; rows are never trimmed post hoc to make metrics agree.

An observed non-target structured predictor is not a row-level eligibility requirement. Once a study is imaging-eligible and its target is observed, a row with every allowed structured predictor missing remains in all modalities and is represented through the locked training medians and eligible missingness indicators. This prevents the structured modality from silently defining a more favorable denominator. The target-specific training feature set itself must still pass the support rules below.

The historical accepted results retain their original denominators and labels. This revalidation rule does not silently rewrite them.

## 3. Panel constructs and target support

The historical `legacy29` list is a support/unit panel, not a leakage-minimized clinical panel. It may be shown only as a historical sensitivity. The Phase 1C clinical work will define three different constructs:

1. strict leakage-minimized measurement completion;
2. target-family-masked report completion; and
3. pragmatic same-report completion.

No final membership is asserted here. `lvef` remains a separate anchor and is not included in a multitask macro average. Unresolved aliases, formula relations, units, or family masks fail closed. Context-only patient/report fields are excluded from the primary echo-measurement macro summary.

The family-masked and pragmatic constructs provisionally use the same scoreable echo-measurement targets as the strict construct; they differ in permitted predictor context, not by promoting BSA, height, blood pressure, or heart rate into primary echo-measurement targets. Those five fields are context-only or a separately labeled metadata benchmark. In the pragmatic construct they may become predictors after review, but exact aliases, duplicates, deterministic formulas, and near-deterministic target reconstruction remain prohibited.

A target can enter a locked scored panel only if its common cohort has at least 120 train, 40 validation, 40 test, and 250 total observed labels, and its training-target IQR is finite and greater than zero. These historical support floors are prespecified independently of performance. No target may be silently removed after model fitting. If a locked primary-panel target later fails an input or metric gate, the run fails and reports the reason; the macro denominator is not changed after test access.

For each binary LVEF definition, train, validation, and test must each contain at least 20 events and 20 nonevents. The primary `<40` analysis blocks if this condition fails. A secondary sensitivity that fails it is not fit and is reported as unsupported, without substitution.

## 4. Masking and preprocessing

The exact target and every prohibited field are removed from the feature schema, not merely set to missing. For each target and panel construct, the order is binding:

1. resolve the target through the locked raw/canonical map and verified unit;
2. remove the exact target, every verified alias/duplicate, and all prohibited deterministic, near-deterministic, or family fields;
3. freeze the allowed raw and canonical predictor allowlist;
4. construct and checksum the common modality denominator;
5. determine feature eligibility from target-specific training rows only;
6. fit training-only median imputation and missing-indicator rules;
7. fit training-only scaling;
8. fit candidate models on training data only;
9. select hyperparameters on validation data only;
10. fit the prespecified validation-only binary calibrator and operating point, where applicable; and
11. evaluate the frozen specification once on test.

Masked fields cannot affect eligibility, imputation, scaling, or missing indicators. Structured and fusion models use the same target-specific structured feature list and transformations. Vision and fusion use the same imaging vector and vision transformation.

### Structured feature eligibility

An allowed numeric predictor is eligible only if, among target-specific training rows, it has:

- at least `max(20, ceil(0.05 * n_train))` finite observed values; and
- at least two distinct finite values.

The 5% rule preserves the historical minimum-coverage convention; the absolute floor prevents unstable medians in smaller target cohorts. Eligibility is not recomputed in validation or test. The exact ordered feature list, exclusions, availability counts, and checksum are recorded for each target and construct.

### Imputation, indicators, and scaling

- Continuous structured features: training median imputation, then training mean/standard-deviation scaling.
- Vision dimensions: training mean/standard-deviation scaling.
- Primary structured and fusion models: add one binary missingness indicator for each eligible structured predictor that has both observed and missing values in the target-specific training set. Indicators are not scaled.
- No indicator is created for a prohibited field, a training-complete field, or an ineligible field.
- A mandatory secondary sensitivity repeats structured and fusion analyses without missingness indicators on the same common denominator.
- For simulated masking of an otherwise allowed field, its indicator changes consistently. Target/family-prohibited fields remain absent and cannot leave an indicator.

These indicators may capture clinical workflow as well as physiology. Their contribution supports report-completion prediction only and is not a causal claim.

## 5. Models and validation-only selection

### Continuous outcomes

All modalities use Ridge regression with alpha grid:

`0.001, 0.01, 0.1, 1, 10, 100, 1000`

Use `fit_intercept = true`, solver `lsqr`, tolerance `1e-8`, and `max_iter = 10000`. For each target, construct, and modality, select the alpha with the lowest full-precision validation MAE. An exact tie selects the larger alpha. The same grid and tie rule apply to vision, structured, and fusion. The selected training-only model is not refit on train plus validation for the primary analysis.

### Binary LVEF choice

Both approaches are prespecified, with different roles:

- **Primary binary analysis:** a separately trained class-weighted logistic regression. This preserves the accepted-abstract model estimand—direct prediction of reduced-LVEF status—and supplies probabilities for discrimination and calibration.
- **Secondary coherence analysis:** threshold the continuous Ridge LVEF prediction at the corresponding EF cutoff. This asks whether continuous report-label completion is directionally consistent with clinical categorization. It does not replace the logistic analysis and is not probability calibrated.

The historical primary label is `lvef < 40`. Separate, explicitly secondary models use `lvef <= 40` and `lvef < 50`; each uses the same locked specification and its own labels. Before fitting or test-performance access, report `n(lvef == 40.0)` using exact parsed numeric equality after the historical subject/measurement median, by all/train/validation/test for both the selected-preimaging and primary-common-imaging-eligible scopes.

The logistic specification is:

- L2 penalty;
- solver `liblinear`;
- `max_iter = 5000`;
- tolerance `1e-4`, `fit_intercept = true`, and random seed `20260801`;
- training-derived `class_weight = balanced`;
- C grid `0.001, 0.01, 0.1, 1, 10, 100, 1000` for every modality;
- select maximum full-precision validation AUROC;
- exact tie selects the smaller C (stronger regularization).

No model or grid is changed after viewing test results.

## 6. Binary calibration and operating point

Post-hoc probability calibration is fixed as Platt sigmoid scaling of the selected model's decision score. The base classifier is fit on training only. After C selection, the unweighted sigmoid calibrator is fit on validation labels only and then frozen. No method comparison is performed. This deliberately reuses the validation set after C selection; calibration is therefore secondary, conditional on the selected model, and potentially optimistic because the validation cohort is limited. It remains preferable to fitting or choosing calibration on test, but it does not provide an independent calibration-validation sample. Uncalibrated and calibrated test probabilities are retained on SCC; AUROC/AP use the selected base score, while Brier score and probability-calibration summaries use calibrated probabilities. If validation has one class or calibration fails a prespecified numerical check, that endpoint fails rather than falling back silently.

Threshold-dependent summaries use a validation-only operating point. Candidate cutoffs are the unique calibrated validation probabilities plus boundary cutoffs. Select the cutoff maximizing Youden's J (`sensitivity + specificity - 1`); ties select greater sensitivity, then greater specificity, then the larger cutoff among classifications that remain identical. Freeze the cutoff before test access. This equal-weights rule is a reproducible research operating point, not a deployment utility threshold. A fixed probability cutoff of 0.5 is secondary and descriptive.

For thresholded continuous regression, the prespecified EF cutoff itself determines the predicted class; there is no learned operating threshold.

## 7. Outcome hierarchy

### Continuous LVEF anchor

1. Primary metric: MAE in EF percentage points.
2. Key secondary: proportion with absolute error `<=5` EF points.
3. Secondary agreement: RMSE, mean signed error, R-squared, Pearson correlation, Spearman correlation, and continuous calibration intercept/slope.
4. Sensitivity tolerance coverage: absolute error `<=4` and `<=8` EF points.
5. Descriptive threshold-band analyses: all cases remain in the primary analysis; report strata inside versus outside 35%–45%, repeat with 37%–43%, and optionally repeat binary summaries after excluding the prespecified band.

The 5-point tolerance, 4/8-point sensitivities, paired-MAE margin of 1.0 point, 0.5/2.0-point margin sensitivities, and 35%–45% or 37%–43% bands are `EXPERT_INFERENCE`. They are not guideline clinical equivalence, MCID, or physiological truth.

### Multitask report completion

- Per-task primary: native-unit MAE.
- Cross-task standardized summary: MAE divided by the training-target IQR.
- Primary locked-panel macro summary: unweighted mean normalized MAE across every prespecified included task, with the fixed task denominator shown.
- Secondary per-task: RMSE, R-squared, Pearson/Spearman correlation, mean signed error, and continuous calibration intercept/slope.
- Secondary summaries: median/IQR normalized MAE, family-balanced means, negative-R-squared count, worst-task table, and win/tie/loss counts only where an endpoint-specific margin has been independently locked.

Native-unit results and denominators remain visible for every task. Normalized MAE and MAE/train-IQR support cross-task description but do not replace native-unit reporting. No shared native-unit margin is inferred across heterogeneous families.

### Binary LVEF

1. Primary discrimination metric for separately trained logistic regression: AUROC for `lvef < 40`.
2. Key secondary: average precision, calibrated Brier score, calibration intercept/slope, and calibration plot summaries.
3. Threshold-dependent secondary: sensitivity, specificity, PPV, NPV, F1, and balanced accuracy at the frozen validation-selected operating point, with prevalence and confusion-matrix counts.
4. Coherence secondary: AUROC/AP using negative continuous predicted LVEF as a score and classification at the exact EF cutoff.
5. Endpoint sensitivities: repeat the prespecified binary framework for `lvef <= 40` and `lvef < 50`; neither can replace `<40` based on results.

## 8. Paired contrasts and bootstrap inference

For every applicable metric, effect orientation is reported explicitly. Calculate all three modality contrasts:

- fusion minus vision;
- fusion minus structured;
- structured minus vision.

The two fusion contrasts are the incremental-value contrasts. Structured minus vision is secondary comparative context.

Use 10,000 nonstratified paired subject bootstrap replicates with seed `20260801`. Models, preprocessing, calibrators, and operating points remain fixed. For LVEF, draw from the locked common LVEF test subjects. For panel summaries, draw once per replicate from the locked imaging-eligible test-subject roster and reuse that subject multiplicity across all modalities and tasks; each task is evaluated only where its label is observed. Thus modality pairing and cross-task missingness dependence are preserved. A panel-macro replicate is invalid if any locked panel task is undefined; it is never recomputed over fewer tasks. Report percentile 95% intervals, valid-replicate count, and undefined/one-class replicate frequency. No metric is silently replaced when a replicate is undefined.

For a prespecified superiority test, let `d` be the observed paired contrast and `d*` the valid bootstrap replicate contrasts. Use a null-centered, two-sided bootstrap p-value: `(1 + count(|d* - d| >= |d|)) / (B_valid + 1)`, capped at one. Keep the percentile confidence interval separate. Report the unadjusted p-value and apply the prespecified Holm procedure within its family. This centered-bootstrap test and percentile interval are approximate inference conditional on the fixed selected models; neither substitutes for an independently replicated study.

These intervals are conditional on the selected fitted models; they do not include model-training variability. Statistical superiority cannot be inferred from separate model-specific intervals. It requires the paired contrast interval and the multiplicity rule below.

## 9. Multiplicity and practical-equivalence language

The hierarchy is fixed:

1. One core confirmatory family contains four fusion incremental-value claims: continuous-LVEF MAE for fusion versus vision and fusion versus structured, plus locked strict-panel mean normalized MAE for fusion versus vision and fusion versus structured. Apply Holm familywise two-sided alpha 0.05 across all four. This provides global FWER control across the manuscript's two core outcomes. The family cannot activate until a genuinely locked strict panel exists.
2. Binary `<40` logistic AUROC fusion contrasts form a separate, explicitly secondary two-contrast Holm family. It is not part of the core global-FWER claim.
3. Structured-versus-vision, alternative metrics/endpoints, pragmatic/family-masked constructs, indicator sensitivity, threshold bands, and subgroup analyses are secondary or exploratory with effect estimates and paired intervals.
4. If task-level inferential p-values are reported, apply Benjamini-Hochberg FDR separately within the prespecified panel and contrast for native-unit MAE; all task estimates and intervals remain visible. No FDR result changes panel membership.

For LVEF paired-MAE differences, the primary expert-inference practical-equivalence margin is `delta = 1.0` EF point; repeat at 0.5 and 2.0 points. With lower MAE favorable:

- margin-exceeding lower error under the expert-inference margin: the whole 95% paired interval is below `-delta`;
- statistically lower error but magnitude not established: the interval is below zero but not wholly below `-delta`;
- practical equivalence under this research margin: the whole interval lies within `[-delta, +delta]`;
- margin-exceeding higher error under the expert-inference margin: the whole interval is above `+delta`;
- otherwise: indeterminate.

Use the phrase “practical equivalence under an expert-inference margin,” not clinical equivalence, MCID, or noninferiority. Non-LVEF win/tie/loss categories remain unavailable until each target's definition, unit, and margin are locked.

## 10. Missingness and masking analyses

- The primary estimand conditions on the target being observed in the report.
- Strict exact-target/alias masking and the appropriate dependency or target-family mask are part of the feature definition, not missing-data imputation.
- Single-field and whole-target-family masking are required constructs where clinically defined.
- Random 10%, 30%, and 50% masking of otherwise allowed structured fields and training-empirical joint-pattern masking are secondary simulations.
- Empirical mask patterns and any information-burden strata are learned from training reports only, then frozen.
- Report primary results with missing indicators and the mandatory no-indicator sensitivity.
- Report performance by prespecified structured-information burden and suppression-safe common missingness patterns.

Natural report missingness is plausibly informative/MNAR. No analysis can estimate accuracy for a naturally unobserved target without independent reference remeasurement. Simulated masking among observed targets estimates recovery of deliberately withheld recorded labels and must not be relabeled as validation on naturally missing truth.

## 11. Subgroup and fairness reporting

Subgroups are chosen without performance inspection. If authorized, linkable source fields exist and aggregate cell-size rules pass, report the continuous-LVEF primary metric, `<40` discrimination/calibration, target availability, and structured-information burden by:

- recorded administrative sex categories; and
- age 18–64, 65–79, and at least 80 years.

Race/ethnicity is exploratory only after the source coding, missing/unknown handling, category mapping, and governance acceptability are locked before test access. It is omitted with an explicit feasibility statement if those conditions fail. No subgroup is merged or dropped because its results appear unfavorable.

Suppress a subgroup metric when `n < 40`; suppress a binary metric when either class has fewer than 10 subjects. Report denominators and uncertainty, but do not make powered fairness-equivalence claims or use subgroup results for model selection. Formal interaction tests and intersectional analyses are exploratory and require a separate multiplicity statement before test access.

## 12. Pre-fit failure rules

Fail before fitting if any of the following is unresolved or false:

- source commit, final config checksum, command checksum, or run-manifest mismatch;
- selected-cohort canonical clip authority or duplicate-key resolution;
- embedding-regeneration decision, checkpoint identity, or new-run environment capture;
- subject split, one-study ownership, imaging eligibility, or exact common-denominator identity;
- raw target definition/unit, alias handling, dependency registry, applicable family mask, or final panel membership;
- target support or binary class-support minimum;
- prohibited field or its missing indicator survives masking;
- structured/fusion feature-list or transformation mismatch;
- preprocessing learned outside training;
- nonfinite values after the declared transform;
- hyperparameter, calibration, operating-point, bootstrap, or multiplicity specification mismatch;
- equality-at-40 aggregate count not recorded before test access;
- required aggregate safety output fails or a restricted output path resolves inside Git;
- a design change is proposed using new test performance; or
- explicit owner authorization is absent.

Model failures and undefined metrics remain visible in aggregate failure tables. No post-test target, row, model, replicate, or metric removal is allowed unless a predeclared rule applies.

## 13. Preservation and aggregate-only output

The immutable historical SCC freeze is not a future preservation authority. Every authorized revalidation must create a new safe-relative-path manifest recording file sizes and SHA-256 values; source commit; command/config/checkpoint checksums; Python, PyTorch, scikit-learn, CUDA, and cuDNN versions; scheduler/job identity; timestamp; cohort/split/panel/model versions; and aggregate safety-gate result.

Git may receive aggregate counts, suppression-safe tables, task/family metrics, paired intervals, configs, environment/checkpoint hashes, and code. Subject, study, clip, and DICOM identifiers; labels; predictions; embeddings; locators; manifests containing restricted rows; and restricted logs remain on SCC.

## 14. Remaining lock conditions

The statistical choices in this SAP do not open confirmatory access. The final SAP and config remain unlocked until `phase1c_pre_revalidation_lock.md` documents passage of every required provenance, duplicate-key, canonical-clip, imaging-eligibility, common-denominator, raw metadata, clinical dependency, panel, margin, safety, checksum, and owner-authorization gate.
