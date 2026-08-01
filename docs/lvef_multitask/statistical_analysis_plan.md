# Statistical analysis plan: LVEF/multitask revalidation

- SAP version: `phase1a-v1.0`
- Date: 2026-08-01
- Branch: `codex/lvef-multitask-revalidation`
- Base commit: `23c74ccfd145ab9a423b6942a431a1894a34ab67`
- Status: **pre-revalidation design lock**

No confirmatory test performance may be generated under this SAP until the raw-name dependency registry, units, common denominators, and clinical adjudication are complete. Amendments must receive a new version/date, document the reason without viewing new test performance, and precede the confirmatory run.

## 1. Historical analysis versus confirmatory revalidation

The accepted version-10 abstract and `2026-04-01_fullscale` snapshot are immutable historical authorities. Historical values will not be overwritten. The confirmatory analysis will be a new dated result set with distinct configs, commands, aggregate outputs, hashes, and claim boundaries.

## 2. Primary question

Among one-study-per-subject MIMIC-IV-ECHO studies with observed quantitative report labels, does adding a frozen whole-study EchoPrime video-encoder representation to leakage-minimized, realistically masked structured measurements improve held-out quantitative report completion over either modality alone on identical subjects?

## 3. Cohort and split estimand

- Primary population: the historical 4,530-patient eligible cohort with one deterministically selected study per patient.
- Split authority: the frozen restricted subject split map, after checksum and integrity audit.
- No subject may appear in more than one split.
- The common evaluation cohort for a target is the intersection of observed target, usable study embedding, and all modality prerequisites.
- Vision-only, structured-only, and early fusion must use identical train, validation, and test IDs and identical target values for every paired comparison.
- Equal counts are insufficient; sorted restricted ID-set hashes and exact set equality are required.
- Repeated-study expansion is excluded from the primary SAP.

## 4. Analysis panels

### Historical panel

`legacy29`: the 29 known-unit/support tasks reported in the accepted abstract. It will be reproduced only as historical sensitivity and must not be called leakage-minimized.

### Primary strict panel

`strict21-v1` provisionally contains:

- LV end-diastolic and end-systolic diameters;
- septal and inferolateral wall thickness;
- RV diameter;
- LA four-chamber length, LA dimension, and RA length;
- sinus, ascending-aorta, and arch diameters;
- AV peak velocity;
- MV peak A and one harmonized MV peak E;
- septal and lateral e′;
- TR peak velocity;
- LVOT VTI and LVOT diameter;
- TAPSE;
- IVC diameter.

The complete target dependency family is removed from structured predictors. Unadjudicated relationships fail closed. Final raw-name membership is locked only after the dependency registry and clinical review are versioned.

### Secondary pragmatic panel

`pragmatic26-v1` contains the strict 21 plus BSA, height, resting heart rate, resting SBP, and resting DBP. Exact aliases and deterministic/near-deterministic derivations remain prohibited. Clinically related nonalgebraic same-report predictors may be allowed after adjudication.

Fractional shortening and TR gradient are deterministic-calculation controls, not scored ML targets. `mv_peak_e` and `mitral_e_velocity` must be merged unless raw provenance disproves duplication.

## 5. Target masking and preprocessing order

For each observed target:

1. Identify the target from the raw panel.
2. Remove the exact target and all raw/canonical aliases.
3. Remove deterministic ancestors/descendants and, for strict analysis, the entire adjudicated target family.
4. Freeze the remaining predictor names and availability mask.
5. Determine feature eligibility from training data only.
6. Fit imputation on training data only.
7. Fit scaling on training data only.
8. Fit candidate models on training data only.
9. Select hyperparameters using validation data only.
10. Evaluate the selected specification once on the locked test set.

Target/family removal must occur before imputation, missing-indicator creation, scaling, or any other transform.

## 6. Predictors and models

- Vision-only: 512-dimensional study mean of frozen EchoPrime video-encoder clip embeddings.
- Structured-only: allowed structured measurements after the applicable mask, training-only feature eligibility, median imputation, missing indicators if prespecified, and standardization.
- Early fusion: concatenated vision and allowed structured blocks, with the same structured mask and train-only preprocessing.
- Primary model class: Ridge regression.
- Binary LVEF sensitivity: class-weighted logistic regression; fitted probabilities are not assumed calibrated.
- No learned pooling, transformer, end-to-end pixel model, modality gating, or generative model is included.

## 7. Validation-only model selection

Ridge alpha grid: `0.001, 0.01, 0.1, 1, 10, 100, 1000`.

For each target and modality, select alpha by minimum validation loss using the target's prespecified primary metric. Ties select the larger alpha. No test metric may influence alpha, feature eligibility, panel membership, threshold, calibration, or exclusion.

The selected train-only fit will be evaluated on test without train+validation refitting in the primary analysis. Any refit analysis must be separately prespecified.

## 8. Estimands and metrics

### LVEF regression anchor

- Primary metric: MAE in EF percentage points.
- Secondary: RMSE, R², Pearson and Spearman correlation, continuous calibration intercept/slope, and clinically adjudicated absolute-tolerance rates.

### Multitask report completion

- Per-task primary metric: MAE divided by the training-target IQR.
- Also report native-unit MAE, RMSE, R², correlation, continuous calibration, and clinically adjudicated tolerance rates.
- Primary panel summary: unweighted mean normalized MAE across the fixed strict panel, with all tasks shown.
- Secondary summaries: median/IQR across tasks, family-balanced means, negative-R² count, worst-task results, and win/loss/tie counts.

### Binary endpoints

- Historical reduced LVEF is `<40%`.
- Primary discrimination measure: AUROC.
- Secondary: average precision, Brier score, calibration intercept/slope, and validation-selected sensitivity/specificity/PPV/NPV.
- A fixed probability threshold of 0.5 is not interpreted clinically without calibration.
- LVEF <50% requires separate approval and is secondary only.

## 9. Paired contrasts and uncertainty

For every target and applicable panel summary, calculate:

- early fusion minus vision-only;
- early fusion minus structured-only;
- structured-only minus vision-only.

Use 10,000 paired subject-level bootstrap replicates. Resample held-out subjects with replacement once per replicate and apply the same draw to all modalities and tasks. Report percentile 95% intervals, the bootstrap seed, valid-replicate count, and any undefined metric frequency. The primary cohort has one study per subject; a repeated-study analysis would carry every study for each sampled subject.

Intervals are conditional on the fixed selected fitted models unless a separately labeled refitting bootstrap is performed. Statistical superiority must not be claimed unless the paired interval for the prespecified contrast excludes the null in the favorable direction and multiplicity rules are satisfied.

## 10. Multiplicity

- Two primary strict-panel modality contrasts—fusion versus vision and fusion versus structured—use Holm adjustment at familywise two-sided α=0.05.
- LVEF is a prespecified clinical anchor and reported with paired intervals; avoid a separate binary `significant/not significant` claim unless explicitly tested under the hierarchy.
- Per-task contrasts are secondary/exploratory. Report adjusted false-discovery-rate q-values if hypothesis tests are supplied, while retaining effect estimates and intervals.
- Clinical equivalence/tie margins will be locked after clinical adjudication and before test access.

## 11. Missingness and masking analyses

- Primary evaluation is conditional on the target being observed in the historical report.
- Single-target and whole-family masking are required.
- Random 10%, 30%, and 50% structured-field masking and empirically sampled joint masks are secondary.
- Empirical mask distributions must be learned from training reports only.
- Report results by structured-information burden and pattern strata.
- Natural missingness is likely informative/MNAR. No accuracy claim is permitted for truly missing labels without independent reference remeasurement.
- Missing indicators may encode workflow/provenance; analyses with and without them must be distinguished.

## 12. Calibration and selective prediction

Optional only after primary revalidation:

- validation-calibrated conformal intervals for continuous targets;
- validation-fixed abstention rules;
- risk–coverage curves;
- interval coverage/width by target family and missingness burden.

No threshold or interval may be tuned on test.

## 13. Failure and exclusion rules

Fail the confirmatory run before model fitting if:

- the worktree commit/config does not match the run manifest;
- split overlap or duplicate assignment exists;
- modality ID sets or target values differ;
- the target or prohibited family survives the mask;
- a preprocessing transform is fit outside training data;
- units or raw-name mappings are unresolved for an included target;
- the target has fewer than the locked minimum train/validation/test counts;
- inputs contain nonfinite values after the declared preprocessing path;
- restricted output resolves inside the Git repository;
- a new test result already exists and an unlogged design change is proposed.

Model-level exclusions and undefined metrics must remain visible in aggregate failure tables; they may not be silently dropped from macro summaries.

## 14. Aggregate-only export policy

Git may receive aggregate counts, suppression-safe missingness tables, task/family metrics, paired-delta intervals, configuration, environment/checkpoint hashes, and code. Subject/study IDs, labels, predictions, embeddings, DICOM paths, manifests, logs, and restricted warnings remain on SCC.

Every new aggregate snapshot requires SHA-256 checksums, source commit, config checksum, command log, data-release identity, environment metadata, checkpoint checksum, and an explicit historical-versus-revalidation label.
