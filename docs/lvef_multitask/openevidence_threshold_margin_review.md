# OpenEvidence threshold and margin review

Status: **pre-revalidation draft; no performance access and no non-LVEF native-unit margin lock**.

This review reconciles the preserved OpenEvidence threshold/margin response with project authority and checked evidence. Machine-readable decisions are in `clinical_threshold_registry_draft.csv` and `clinical_margin_registry_draft.csv`.

## Quality ruling

The OpenEvidence response cannot be imported. Exact identifiers are star-mangled, citation numbers interrupt names and sentences, the LVEF ruling loses text around its inequalities, the task CSV truncates during `lat_e_prime`, and figure/reference material is inserted into CSV rows. Its proposed native-unit values for several non-LVEF tasks are low-certainty transfers across populations or related measurements. They are not locked here.

The following safeguards apply:

- only exact hard-allowlisted repository target strings enter either draft registry;
- population reference-range standard deviations are not reader-repeatability estimates;
- ICC without the underlying scale is not converted into a native-unit error tolerance;
- variability of one endpoint, view, or modality is not transferred to another;
- a reference measurement's limits of agreement, least significant change, MCID, and a model-comparison tie margin are not treated as interchangeable;
- no threshold or margin is selected from model performance.

## LVEF endpoint lock candidates

### Binary endpoints

1. Historical primary: `lvef < 40`. This exact strict inequality is accepted-abstract version-10 authority and remains primary for fidelity.
2. Secondary sensitivity: `lvef <= 40`. This aligns with the checked reduced-EF guideline boundary but does not replace the historical primary.
3. Secondary guideline-classification sensitivity: `lvef < 50`. This is prespecified only to examine labels below the preserved-EF boundary and is not a performance-selected alternative.

Before any test-performance access, an aggregate label audit must report the count of labels equal to exactly 40% overall and by frozen split. That count is descriptive endpoint provenance, not a model result. The three endpoint columns must then be generated deterministically from one locked continuous label using the displayed inequalities.

### Continuous anchor, tolerance coverage, and tie categories

Continuous LVEF remains the primary quantitative anchor. Candidate prespecifications are:

- primary tolerance coverage: absolute error `<=5` EF percentage points;
- sensitivity tolerance coverage: `<=4` and `<=8` EF points;
- primary paired-MAE practical tie/equivalence category: absolute paired MAE difference `<=1.0` EF point;
- sensitivity tie categories: `<=0.5` and `<=2.0` EF points;
- descriptive near-threshold band: 35%-45%;
- optional narrower band sensitivity: 37%-43%.

Every value above is `EXPERT_INFERENCE`. None is a guideline-defined clinical equivalence limit, MCID, physiological truth threshold, or validated noninferiority margin. The practical tie/equivalence categories become formal equivalence tests only if the statistical plan separately prespecifies an estimand, two one-sided testing or confidence-interval rule, alpha, and multiplicity handling. Otherwise they are descriptive paired-difference categories.

The primary analysis retains all eligible labels, including labels within 35%-45%. The band may be used only for prespecified secondary stratification or band-exclusion sensitivity. It must not be used to make the primary task easier.

### Report-label truth boundary

The project evaluates agreement with a structured report label whose acquisition method may be visual, biplane, 3-D, Teichholz/linear, contrast-enhanced, rounded, or mixed. Until CMR-08 resolves that composition, the result cannot be described as accuracy against physiological truth or a single expert-reference method. Method-stratified descriptive analysis is allowed only if method provenance is independently available and the strata are prespecified before test access.

## Non-LVEF decisions

No non-LVEF native-unit model margin is locked. Each requires verified project definition, unit, method/view/timing, and endpoint-specific agreement evidence. The following OpenEvidence proposals were deliberately not promoted:

- dimensional margins inferred from normative population SD;
- AV-peak-velocity margins inferred from AV VTI variability;
- any universal margin shared across cm/mm dimensions, m/s/cm/s velocities, percentages, pressure, and metadata;
- thresholds from multiparametric diagnostic algorithms treated as standalone binary targets;
- low-certainty values for LVOT diameter or VTI treated as clinical equivalence limits.

The locally supplied Jozwiak et al. study supports only relative VTI repeatability context: median least significant change of 11% for successive examinations by the same operator and 14% with different operators in 100 stable ICU patients. It does not justify an absolute `cm` report-label margin or a paired model tie margin. These two evidence rows are retained as `CONTEXT_ONLY` in the margin registry.

For every scored non-LVEF target, future reporting should include the verified native unit and native-unit MAE with a paired interval. Normalized MAE or MAE divided by training-set IQR may remain cross-task summaries after train-only scale estimation, but they cannot replace native-unit estimates or turn heterogeneous tasks into a shared clinical margin.

## Threshold interpretation boundary

No non-LVEF binary threshold is necessary for the primary report-completion question. Reference cutoffs for aortic stenosis, pulmonary-hypertension probability, diastolic algorithms, right-heart findings, and chamber/aortic size depend on method, units, body-size/sex/age indexing, rhythm, ventilation, loading, or other measurements. They remain outside the current endpoint lock unless a later protocol adds a clinically motivated and fully specified secondary classification analysis before performance access.

`body_surface_area`, `height_cm`, `resting_sbp`, `resting_dbp`, and `resting_hr` are context/metadata targets and do not enter the primary echo-measurement macro. Their clinical reference thresholds do not make them image-derived measurement endpoints.

`fs` is formula-only only if the restricted review confirms the standard definition. `tr_mmhg` is formula-only only if it is a peak TR gradient. `mitral_e_velocity` has no independent threshold or margin until its identity relative to `mv_peak_e` is resolved.

## Evidence status

- Verified repository authority: accepted abstract version 10 for `lvef <40`.
- Verified official primary/organization sources: ASE/EACVI 2015 EF method/formula; ASE 2025 TR/RVSP formula; ACC 2022 heart-failure EF categories.
- Verified supplied full text: Jozwiak et al. 2019 VTI least significant change context.
- Unverified: all other OpenEvidence-attached citations that were not supplied or independently checked.
- Expert inference: every LVEF model tolerance, tie category, and uncertainty band specified by Phase 1C.

These drafts may be owner-locked only after the continuous-label unit is verified as EF percentage points, exact-40 counts are produced without accessing performance, and the statistical plan agrees with the registry. Non-LVEF margins remain open after that lock unless their own evidence gates are resolved.
