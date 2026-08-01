# OpenEvidence clinical adjudication request

## Role and evidence standard

Act as an evidence-focused clinical echocardiography panel with expertise in cardiovascular imaging, critical care echocardiography, perioperative medicine, Doppler hemodynamics, and quantitative measurement standards.

Do not assume that our provisional target panels or dependency classifications are correct. Adjudicate every relationship using guideline, standards, or peer-reviewed evidence. Cite each substantive decision, state uncertainty explicitly, and distinguish exact mathematical dependence from physiologic correlation.

## Study context

We are evaluating frozen whole-study echocardiographic representations and available same-report structured measurements for quantitative report-label completion in MIMIC-IV-ECHO. The historical analysis excluded only the exact canonical target from the structured predictors. We need to separate:

1. inappropriate target leakage;
2. strict leakage-minimized prediction;
3. pragmatic same-report completion.

The historical LVEF binary endpoint is LVEF <40%. No direct caliper/trace localization, prospective clinical deployment, patient-outcome prediction, or generative model is being evaluated.

The separate primary anchor target is exact raw/canonical `lvef`. Please adjudicate all LVEF aliases and its LV volume, dimension, fractional-shortening, wall-motion, qualitative-function, stroke-volume, and cardiac-output dependency families in addition to the 29 multitask targets below.

## Exact historical 29 task names

1. `fs`
2. `tr_mmhg`
3. `tricuspid_regurgitant_peak_velocity`
4. `left_ventricular_end_systolic_diameter`
5. `left_ventricular_end_diastolic_diameter`
6. `body_surface_area`
7. `height_cm`
8. `av_pk_vel`
9. `sept_e_prime`
10. `la_4ch_length`
11. `ra_length`
12. `la_dimen`
13. `sinus_diam`
14. `resting_hr`
15. `inf_lat_thickness`
16. `mv_peak_a`
17. `mv_peak_e`
18. `septal_thickness`
19. `lvot_vti`
20. `ascending_aorta_diameter`
21. `rv_diam`
22. `lvot_diam`
23. `mitral_e_velocity`
24. `tricuspid_annular_plane_systolic_excursion`
25. `arch_diam`
26. `resting_dbp`
27. `resting_sbp`
28. `ivc_diam`
29. `lat_e_prime`

The initial repository-derived candidate predictor names are the same 29 canonical names. A restricted SCC packet will add every raw measurement name, description, unit, and raw-to-canonical mapping. Treat names such as `tr_mmhg`, `mv_peak_e`, and `mitral_e_velocity` as unresolved until their raw descriptions and units are reviewed.

## Required relationship categories

Assign one of:

- `DIRECT_TARGET`
- `SYNONYM_OR_DUPLICATE`
- `DETERMINISTIC_DERIVATIVE`
- `NEAR_DETERMINISTIC_CLINICAL_DERIVATIVE`
- `SAME_REPORT_CORRELATE`
- `INDEPENDENT_STRUCTURED_PREDICTOR`
- `UNCERTAIN_REQUIRES_CLINICAL_REVIEW`

## Questions

1. Which target/predictor relationships are mathematically deterministic?
2. Which are near-deterministic through standard echocardiographic formulas or indexing conventions?
3. Which relationships constitute unacceptable leakage even in pragmatic report completion?
4. Which nonalgebraic same-report predictors are reasonable for a pragmatic completion use case but should be removed from a strict panel?
5. Which measurements form coherent clinical dependency families?
6. Which tasks have the strongest relevance to cardiovascular imaging, critical care, perioperative medicine, and hemodynamic monitoring?
7. What clinically meaningful absolute-error tolerances should be reported for each task family, and what evidence supports them?
8. Which measurement-specific binary thresholds are guideline-supported and sufficiently stable for prespecification?
9. Is LVEF <40% appropriate as the preserved historical endpoint?
10. Would LVEF <50% add clinical value only as an explicitly secondary sensitivity?
11. Which endpoints should be removed because of duplication, deterministic calculation, low clinical relevance, ambiguous units, or unstable measurement definitions?
12. Which raw aliases must be merged for mitral E, LV dimensions, wall thickness, aortic segments, TR velocity/gradient, LVOT VTI/diameter, TAPSE, IVC/RAP, and tissue-Doppler e′?

Please explicitly review these provisional formula families:

- `FS = (LVEDD − LVESD) / LVEDD × 100`
- `peak pressure gradient = 4 × velocity²`, with unit reconciliation
- LVOT diameter → cross-sectional area; area × LVOT VTI → stroke volume
- stroke volume × heart rate → cardiac output; cardiac output / BSA → cardiac index
- MV E / e′ and MV E / MV A ratios
- IVC diameter/collapse → estimated RAP; TR gradient + RAP → RVSP/PASP
- LV dimensions and wall thicknesses → LV mass; raw values / BSA → indexed quantities
- SBP/DBP → pulse pressure and estimated MAP

## Required output table

Return one row per adjudicated target/predictor relationship with:

| Field | Required content |
|---|---|
| target | Exact supplied target name |
| predictor_raw_name | Exact raw predictor name |
| predictor_canonical_name | Proposed canonical name |
| target_family | Clinically coherent family |
| relationship_category | One permitted category |
| formula_or_rationale | Formula or clinical rationale |
| strict_predictor_allowed | Yes/no |
| pragmatic_predictor_allowed | Yes/no |
| target_should_be_scored | Strict/pragmatic/formula-only/remove |
| clinically_meaningful_tolerance | Value, unit, and context |
| binary_threshold | Threshold and intended use, if supported |
| evidence | Direct citations/links |
| certainty | High/moderate/low |
| unresolved_question | Remaining uncertainty |

Also return a short family-level evidence table and an explicit list of relationships that cannot be adjudicated without inspecting raw descriptions, units, or acquisition context.
