# OpenEvidence prompt 1: dependency, leakage, and task-family adjudication

Copy everything below this line into OpenEvidence as one request.

---

Act as an evidence-focused clinical echocardiography adjudication panel with expertise in cardiovascular imaging, critical care echocardiography, perioperative medicine, Doppler hemodynamics, and quantitative measurement standards.

## Purpose and decision boundary

We are revalidating a historical MIMIC-IV-ECHO analysis of frozen whole-study EchoPrime video-encoder representations plus same-report structured measurements. The intended current use case is **quantitative structured report-label completion**, not direct caliper/trace localization, prospective deployment, patient-outcome prediction, or generative report production.

Your task is to adjudicate clinical and formula-based dependency, leakage, aliasing, and task-family relationships. Do not use model performance, correlations learned from these data, feature importance, or the accepted abstract's results to decide whether a relationship exists, whether a target is clinically important, or whether a predictor is permissible.

The project distinguishes three analysis concepts:

1. **strict leakage-minimized prediction**: remove the exact target, every raw/canonical alias, every deterministic or near-deterministic ancestor/descendant, and other target-family variables that would make the task a same-report proxy exercise;
2. **target-family-masked report completion**: simulate a missing target by masking the target and the clinically coherent family needed to prevent family-level shortcutting;
3. **pragmatic same-report completion**: permit clinically related but nonalgebraic same-report context only when it is a plausible real report-completion input, while still prohibiting exact aliases, duplicates, deterministic derivatives, and near-deterministic reconstructions.

Our provisional `strict21-v1` and `pragmatic26-v1` panels, provisional family assignments, and relationship suggestions below are **hypotheses, not authorities**. Do not accept them as gospel. Revise or reject them when the evidence requires. Do not finalize either panel for us; instead, identify what is supported, what remains unresolved, and what evidence or metadata are still required.

## Available project metadata and its limits

The historical source contains 186 raw measurement names mapped to 178 canonical tasks. The restricted raw-to-canonical mapping authority has 188 rows and these fields: `measurement`, `measurement_clean`, `measurement_description`, `description_clean`, `canonical_measurement`, `canonical_source`, `unit`, `unit_norm`, and `unit_category`. The restricted canonical registry also has recommended canonical units and raw-name summaries. Those row values were deliberately not exported in the aggregate review packet. Therefore:

- the exact raw aliases and their dataset units are not supplied here;
- the current repository-level dependency registry records most units as `UNKNOWN`;
- you may identify expected standard names, aliases, definitions, and units from authoritative literature, but you must not claim that a proposed alias or unit is present in our data;
- any decision that requires a raw description, acquisition method, view, timing convention, or unit must remain `UNRESOLVED` pending restricted clinical review of that metadata.

Phase 1A established that the historical code excluded the exact canonical target, but it did **not** establish complete raw-synonym exclusion or formula-family exclusion. For every target, the provisional evidence matrix still records `raw_synonyms_complete = False`, `formula_review_complete = False`, and clinical review pending. Exact-target removal alone is not evidence that leakage was controlled.

## Exact historical target terminology

The separate primary continuous anchor is exact canonical `lvef`. Its historical primary binary endpoint is **LVEF <40%**.

The exact `legacy29` canonical task names, in the verified strict-panel packet order, are below. The ordering itself has no scientific meaning.

1. `body_surface_area`
2. `resting_sbp`
3. `resting_dbp`
4. `resting_hr`
5. `left_ventricular_end_diastolic_diameter`
6. `septal_thickness`
7. `inf_lat_thickness`
8. `la_dimen`
9. `sinus_diam`
10. `mv_peak_e`
11. `mitral_e_velocity`
12. `av_pk_vel`
13. `la_4ch_length`
14. `ra_length`
15. `ascending_aorta_diameter`
16. `lvot_diam`
17. `rv_diam`
18. `lvot_vti`
19. `mv_peak_a`
20. `tr_mmhg`
21. `tricuspid_regurgitant_peak_velocity`
22. `left_ventricular_end_systolic_diameter`
23. `lat_e_prime`
24. `sept_e_prime`
25. `arch_diam`
26. `fs`
27. `ivc_diam`
28. `height_cm`
29. `tricuspid_annular_plane_systolic_excursion`

Use these strings exactly in every output field. Do not silently rename them. You may recommend a display label or a harmonized canonical name in a separate field.

The current evidence matrix proposes, but does not establish, these dispositions:

- `lvef`: separate primary anchor, not a multitask-panel member;
- formula-only rather than scored ML targets: `fs` and `tr_mmhg`;
- possible duplicate to merge: `mitral_e_velocity` into `mv_peak_e`;
- provisional `strict21-v1`: `tricuspid_regurgitant_peak_velocity`, `left_ventricular_end_systolic_diameter`, `left_ventricular_end_diastolic_diameter`, `av_pk_vel`, `sept_e_prime`, `la_4ch_length`, `ra_length`, `la_dimen`, `sinus_diam`, `inf_lat_thickness`, `mv_peak_a`, `mv_peak_e`, `septal_thickness`, `lvot_vti`, `ascending_aorta_diameter`, `rv_diam`, `lvot_diam`, `tricuspid_annular_plane_systolic_excursion`, `arch_diam`, `ivc_diam`, and `lat_e_prime`;
- provisional `pragmatic26-v1`: the provisional strict 21 plus `body_surface_area`, `height_cm`, `resting_hr`, `resting_dbp`, and `resting_sbp`.

Every one of these dispositions remains pending raw-alias, unit, formula, OpenEvidence, and clinician adjudication.

## Provisional family hypotheses to audit

These memberships are starting questions only. For each family, state whether its members are coherent, whether the family should be split or expanded, and whether each member should be masked for each target under strict, target-family-masked, and pragmatic analyses.

| Provisional family | Provisional members |
|---|---|
| `lv_systolic_geometry` | `lvef`; `fs`; `left_ventricular_end_systolic_diameter`; `left_ventricular_end_diastolic_diameter` |
| `tr_pulmonary_pressure` | `tr_mmhg`; `tricuspid_regurgitant_peak_velocity`; `ivc_diam` |
| `anthropometrics` | `body_surface_area`; `height_cm` |
| `aortic_valve_flow` | `av_pk_vel`; `lvot_vti`; `lvot_diam` |
| `mitral_diastolic` | `sept_e_prime`; `lat_e_prime`; `mv_peak_a`; `mv_peak_e`; `mitral_e_velocity` |
| `atrial_geometry` | `la_4ch_length`; `la_dimen`; `ra_length` |
| `aortic_geometry` | `sinus_diam`; `ascending_aorta_diameter`; `arch_diam` |
| `lv_wall_geometry` | `septal_thickness`; `inf_lat_thickness`; `left_ventricular_end_diastolic_diameter` |
| `rv_structure_function` | `rv_diam`; `tricuspid_annular_plane_systolic_excursion`; `tricuspid_regurgitant_peak_velocity`; `ivc_diam` |
| `hemodynamic_context` | `resting_hr`; `resting_dbp`; `resting_sbp`; `body_surface_area` |

Specifically consider whether the broad families above should be replaced by smaller **formula dependency sets** and wider **clinical correlation families** rather than treating every member of one family as equivalent.

## Required relationship taxonomy

Assign exactly one primary category to each adjudicated target/predictor relationship:

- `DIRECT_TARGET`
- `SYNONYM_OR_DUPLICATE`
- `DETERMINISTIC_DERIVATIVE`
- `NEAR_DETERMINISTIC_CLINICAL_DERIVATIVE`
- `SAME_REPORT_CORRELATE`
- `INDEPENDENT_STRUCTURED_PREDICTOR`
- `UNCERTAIN_REQUIRES_CLINICAL_REVIEW`

Use the following distinctions:

- `DETERMINISTIC_DERIVATIVE` means one value can be algebraically recovered from the other supplied field or a specified set of supplied fields under explicit unit conversion and stated assumptions.
- `NEAR_DETERMINISTIC_CLINICAL_DERIVATIVE` means a standard clinical formula, indexing convention, tightly coupled alternate measurement method, or small set of same-report fields makes reconstruction sufficiently direct to threaten the intended task, but the relationship is not exact in all acquisition/method contexts.
- `SAME_REPORT_CORRELATE` means clinically related but neither synonymous nor algebraically reconstructive.
- `INDEPENDENT_STRUCTURED_PREDICTOR` means no clinically meaningful target-family, formula, alias, or same-construct relationship is supported; it does not mean statistically independent in these data.
- when definitions, units, raw descriptions, or acquisition context are insufficient, use `UNCERTAIN_REQUIRES_CLINICAL_REVIEW` rather than guessing.

For dependency direction, use one of: `PREDICTOR_TO_TARGET`, `TARGET_TO_PREDICTOR`, `BIDIRECTIONAL_EQUIVALENCE`, `MULTIVARIABLE_SET_TO_TARGET`, `COMMON_CONSTRUCT_NO_FORMULA`, `NONE_SUPPORTED`, or `UNRESOLVED`.

## Relationships and formula families that require explicit review

Do not limit your review to the provisional classifications. At minimum, address all of the following and identify any missing relationship relevant to these supplied tasks.

### LVEF and LV systolic geometry

- Distinguish exact LVEF aliases/duplicate exports from alternative systolic-function measures.
- State when `LVEF = (EDV - ESV) / EDV × 100` is deterministic and what is required for EDV and ESV to be compatible.
- Distinguish volume-derived LVEF from linear `fs = (LVEDD - LVESD) / LVEDD × 100`.
- Decide whether `fs`, `left_ventricular_end_systolic_diameter`, and `left_ventricular_end_diastolic_diameter` are deterministic, near-deterministic, or only same-construct correlates for `lvef`, alone and in combination.
- Identify raw/canonical concepts outside `legacy29` that must be sought in the restricted registry for LVEF masking, including ejection-fraction aliases, LV end-diastolic/end-systolic volumes, qualitative LV systolic function, wall-motion summaries, Simpson/biplane or 3-D outputs, stroke volume, and cardiac output. Mark name-pattern suggestions as candidates, not confirmed data fields.

### TR velocity, TR gradient, IVC, and pulmonary-pressure estimates

- Review `tr_mmhg` versus `tricuspid_regurgitant_peak_velocity`, including `ΔP = 4v²`, the required velocity unit, and whether `tr_mmhg` denotes a peak TR gradient or something else pending its raw description.
- Review `RVSP/PASP = 4v² + estimated RAP` and the assumptions under which RVSP approximates PASP.
- Review IVC diameter and respiratory collapse as inputs to estimated right atrial pressure, noting that `ivc_diam` alone may be insufficient.
- Separate deterministic gradient calculation, categorical RAP estimation, and broader RV/pulmonary-pressure correlation.

### LVOT, aortic flow, and derived hemodynamics

- Review `LVOT area = π × (LVOT diameter)² / 4`, `stroke volume = LVOT area × LVOT VTI`, `cardiac output = stroke volume × heart rate`, and `cardiac index = cardiac output / body surface area`.
- State the units and conversions required at every step and the assumptions about measurement site, timing, rhythm, beat averaging, and diameter convention.
- Decide whether `lvot_diam`, `lvot_vti`, `resting_hr`, and `body_surface_area` should be masked singly or as a set when any directly derived output is the target, and whether they are merely same-report correlates when the target is `av_pk_vel`.
- Distinguish LVOT flow from transaortic peak velocity; do not treat `av_pk_vel` as algebraically recoverable unless the evidence and supplied fields support that conclusion.

### Mitral inflow and tissue Doppler

- Determine whether `mv_peak_e` and `mitral_e_velocity` are synonyms/duplicate exports or must remain unresolved until raw descriptions, timing, and units are reviewed.
- Review E/A (`mv_peak_e / mv_peak_a`) and septal, lateral, and averaged E/e′ formulations using `sept_e_prime` and `lat_e_prime`.
- State when these are deterministic ratios and when their use as surrogates for filling pressure is only clinical association.
- Distinguish transmitral E velocity from annular e′ velocity and require unit harmonization.

### LV wall geometry and mass

- Review `septal_thickness`, `inf_lat_thickness`, and `left_ventricular_end_diastolic_diameter` as inputs to guideline LV-mass formulas.
- State the formula variant, units, phase of measurement, and whether `inf_lat_thickness` can be assumed to represent posterior-wall thickness; if that cannot be established from the name alone, mark it unresolved.
- Review indexing by BSA or height and distinguish raw dimensions from indexed values.

### Anthropometrics and blood pressure

- Review common BSA formulas and explain why `height_cm` alone is or is not sufficient to derive `body_surface_area`; identify the missing inputs and units.
- Review pulse pressure and estimated mean arterial pressure derived from `resting_sbp` and `resting_dbp`, including assumptions that make simplified MAP formulas approximate rather than universally deterministic.
- Distinguish formula-based derivatives from general hemodynamic context involving `resting_hr`.

### Chamber and aortic geometry

- Review whether different LA, RA, RV, aortic-root/sinus, ascending-aortic, and arch linear dimensions represent distinct anatomic constructs, alternate views of the same construct, or possible duplicate exports.
- Identify when body-size indexing creates a near-deterministic relationship with `body_surface_area` or `height_cm`.
- Do not infer interchangeability merely because two variables share a broad geometry family.

### RV structure and longitudinal function

- Distinguish `rv_diam`, `tricuspid_annular_plane_systolic_excursion`, TR velocity/gradient, and `ivc_diam` as separate structure, systolic-function, pressure, and venous-context constructs.
- State which pairs are same-report correlates and which, if any, should be excluded together under target-family masking.

## Required adjudication questions

1. For every supplied target, which raw aliases, canonical duplicates, formula ancestors, formula descendants, and same-construct measurements should the restricted clinical review search for?
2. Which relationships are exact only under specific units or acquisition assumptions? State the formula, input/output units, conversion, assumptions, and direction.
3. Which variables must be excluded as predictors for each target in:
   - strict leakage-minimized prediction;
   - target-family-masked report completion;
   - pragmatic same-report completion?
4. Which nonalgebraic same-report predictors could be permissible only in the pragmatic analysis?
5. Which variables should never be scored as separate ML targets because they are duplicates or deterministic calculations? Which could instead serve as formula-only sanity controls?
6. Are provisional exclusions of `fs` and `tr_mmhg` from scored ML macro summaries justified, and is provisional merging of `mitral_e_velocity` into `mv_peak_e` justified? State what raw metadata would be needed before finalizing each decision.
7. Are `body_surface_area`, `height_cm`, `resting_hr`, `resting_dbp`, and `resting_sbp` coherent report-completion targets, context-only targets, or out-of-scope patient/report metadata? Treat this as a clinical/use-case question, not a performance question.
8. Which family memberships should be split, expanded, renamed, or left unresolved?
9. Which relationships remain uncertain after literature review, and exactly why?

## Evidence rules

For every substantive relationship or recommendation, assign the strongest applicable support tier:

1. `PROFESSIONAL_GUIDELINE_OR_CONSENSUS`
2. `PEER_REVIEWED_MEASUREMENT_METHODOLOGY`
3. `PEER_REVIEWED_CLINICAL_OBSERVATIONAL`
4. `FORMULA_BASED_INFERENCE`
5. `EXPERT_INFERENCE`
6. `UNRESOLVED`

Prefer current professional society guidelines/standards, but retain older measurement standards when they are the authoritative source for a formula. Provide complete citations, year, journal/organization, DOI and PMID where available, plus a direct link. Cite the exact source supporting each decision; do not attach a generic review to unrelated rows. If evidence conflicts, describe the conflict and do not collapse it to false certainty.

Absence of literature found is not proof that no relationship exists. Every unsupported recommendation must remain `UNRESOLVED`. Formula-based or expert inference must be labeled as such and must not be presented as guideline authority.

## Required response format

Return all five components below.

### 1. Executive adjudication

In no more than 800 words, identify the highest-risk leakage pathways, family hypotheses that should be revised, and decisions that remain blocked by missing raw descriptions/units.

### 2. Formula dependency graph

Provide a directed, text-readable graph with separate nodes for observed measurements and derived quantities. Label every edge with its formula, units, assumptions, and evidence tier. Do not imply direction for relationships that are only correlations.

### 3. Machine-readable relationship registry

Return a UTF-8 CSV code block with exactly one row per **clinically relevant** target/predictor relationship. Include all exact target self-relationships, all supported or suspected aliases/duplicates, every deterministic or near-deterministic relationship, every within-family relationship that affects masking, and every relationship you leave unresolved. You do not need to manufacture rows for obviously unrelated cross-family pairs; list those exclusions and the rule used in the methods note.

Use exactly these columns and values:

```text
target,predictor_raw_name,predictor_canonical_name,target_family,relationship_category,dependency_direction,formula,formula_input_units,formula_output_unit,assumptions,strict_predictor_allowed,family_mask_predictor_allowed,pragmatic_predictor_allowed,strict_target_disposition,pragmatic_target_disposition,evidence_tier,citation_short,doi,pmid,certainty,unresolved_reason,restricted_metadata_needed,recommended_registry_action
```

Rules for the CSV:

- `target` must be `lvef` or one exact `legacy29` string.
- use `Yes`, `No`, or `Unresolved` in each predictor-allowed field;
- use `Score`, `Formula-only`, `Merge`, `Context-only`, `Remove`, or `Unresolved` in target-disposition fields;
- use one permitted relationship category and one permitted dependency direction;
- place `NA` rather than inventing a formula, DOI, PMID, or unit;
- if a predictor is a literature-derived name-pattern candidate not confirmed in our registry, prefix `predictor_raw_name` with `CANDIDATE_PATTERN:`;
- keep citations concise in the CSV and give full references in component 5.

### 4. Machine-readable family adjudication

Return a second UTF-8 CSV code block with these columns:

```text
family_name,member_exact_names,formula_dependency_set,clinical_correlation_set,recommended_split_or_expansion,strict_family_mask_rule,pragmatic_mask_rule,clinical_domains,evidence_tier,certainty,unresolved_metadata
```

For `clinical_domains`, use any supported combination of `cardiovascular_imaging`, `critical_care`, `perioperative_medicine`, and `hemodynamic_monitoring`, or `none_supported`. Do not rank a domain from model performance.

### 5. Evidence and unresolved appendix

Provide complete references with DOI/PMID where available, map each reference to the relevant CSV rows, and end with:

- an explicit list of decisions that can be locked now;
- an explicit list that must remain provisional;
- a restricted metadata checklist for the clinician reviewing raw names, descriptions, units, view/method, timing, and indexing conventions.

Do not claim that the provisional panels are validated, and do not output a final locked `strict21-v1` or `pragmatic26-v1` panel. We will review your evidence with a clinician and update the dependency registry before any confirmatory test access.
