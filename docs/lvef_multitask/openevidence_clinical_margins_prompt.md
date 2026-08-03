# OpenEvidence prompt 2: clinical relevance, thresholds, variability, and margins

Copy everything below this line into OpenEvidence as one request. This request is intentionally self-contained and may be run independently of the dependency/leakage prompt.

---

Act as an evidence-focused clinical echocardiography and perioperative/critical-care panel with expertise in quantitative measurement standards, reproducibility, clinical decision thresholds, and model-evaluation methodology.

## Purpose and guardrails

We are prespecifying clinically interpretable evaluation criteria for a revalidation of quantitative structured report-label completion in MIMIC-IV-ECHO. Models use frozen whole-study EchoPrime video-encoder representations and/or appropriately masked same-report structured measurements. This is not direct caliper/trace localization, prospective deployment, patient-outcome prediction, or generative report production.

Your task is to adjudicate clinical relevance, guideline-supported binary thresholds, expected measurement variability, clinically meaningful absolute-error tolerances, and model-comparison equivalence/tie margins. Do not use our historical or current model performance to select endpoints, tasks, thresholds, margins, or clinical priorities.

The accepted historical binary endpoint is **LVEF <40%**. It must remain the historical primary binary endpoint for fidelity unless there is a compelling validity problem. Evaluate whether LVEF <50% has a role **only as an explicitly secondary sensitivity**, not as a performance-selected replacement. Do not propose a threshold because it is likely to improve discrimination.

Our provisional task panels and family assignments are hypotheses, not authorities. Do not accept them as gospel and do not return a final locked task panel. Absence of literature found is not proof that no relationship, threshold, or meaningful margin exists. Unsupported recommendations must remain unresolved.

## Exact project targets

The separate continuous anchor is exact canonical `lvef`. The exact `legacy29` canonical task names, in the verified strict-panel packet order, are below. The ordering itself has no scientific meaning.

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

Use these strings exactly in all machine-readable outputs. You may recommend a clinical display label separately. Dataset-specific raw descriptions and units are not supplied here; repository-level dependency records currently mark most units `UNKNOWN`. State expected guideline units, but mark any dataset-unit or measurement-definition conclusion unresolved until restricted raw metadata are reviewed.

The current evidence matrix provisionally treats `lvef` as a separate primary anchor; `fs` and `tr_mmhg` as formula-only controls; `mitral_e_velocity` as a possible duplicate to merge into `mv_peak_e`; 21 echo measurements as provisional `strict21-v1`; and `body_surface_area`, `height_cm`, `resting_hr`, `resting_dbp`, and `resting_sbp` as five additional provisional `pragmatic26-v1` context targets. These are hypotheses awaiting raw-alias, unit, formula, literature, and clinician adjudication—not settled dispositions.

## Provisional task-family hypotheses to audit

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

Audit these families for clinical coherence. Do not assign one margin to a broad family when acquisition method, unit, scale, or reproducibility differs materially among its members.

## Required distinctions for margins

For every target, keep these concepts separate:

1. **reference measurement variability**: interobserver/intraobserver repeatability, test-retest variability, or published limits of agreement for the clinical measurement itself;
2. **clinically meaningful absolute-error tolerance**: a prespecified native-unit band within which a model estimate might be considered acceptably close for this report-completion research use case;
3. **model-comparison equivalence/tie margin**: the largest clinically negligible paired difference between models in native-unit MAE, normalized MAE, or tolerance rate;
4. **binary near-threshold uncertainty band**: if evidence supports it, a range around a threshold in which measurement variability makes hard classification unstable.

Do not derive a clinical tolerance mechanically from model error, the observed data distribution, IQR, or a convenient round number. Do not treat correlation, R², or statistical non-significance as clinical agreement or equivalence. A Bland–Altman limit of agreement, minimal detectable change, reference change value, acceptable interreader difference, and minimal clinically important difference are not interchangeable; label the evidence construct accurately.

If evidence supports measurement variability but not a model-error or equivalence margin, report the variability and leave the margin unresolved. If a family-level margin is defensible only after unit normalization or indexing, specify the transformation and assumptions.

## Clinical relevance questions

For `lvef` and every exact `legacy29` task:

1. What does the measurement represent, and what acquisition method, cardiac phase, view, beat selection, indexing convention, and standard unit are required to interpret it?
2. Is it directly relevant, contextually relevant, or not clearly relevant to each of:
   - cardiovascular imaging;
   - critical care;
   - perioperative medicine;
   - hemodynamic monitoring?
3. Is prediction of an observed structured report label a clinically plausible report-completion task, a formula/metadata reconstruction, an artificial benchmark, or unresolved?
4. Would a clinically useful application require direct image localization/remeasurement rather than label completion? State this limitation explicitly where applicable.
5. Is the measurement sufficiently standardized to compare errors across studies without knowing method/view/indexing details?
6. Should it be a scored endpoint, a context-only target, a formula-only control, merged with another target, removed, or left unresolved? Do not make this recommendation from model performance.

Pay particular attention to whether `body_surface_area`, `height_cm`, `resting_hr`, `resting_dbp`, and `resting_sbp` are quantitative echo-report targets, patient/hemodynamic context, or artificial report-label benchmarks. Also assess whether `fs` and `tr_mmhg` are better treated as formula-only controls and whether `mv_peak_e` and `mitral_e_velocity` require raw-metadata confirmation before merging.

## Threshold questions

For each target, report a binary or ordinal threshold only when a professional guideline/consensus or strong measurement standard supports both the cutoff and its intended clinical interpretation. For every supported threshold, provide:

- exact inequality and unit;
- population and intended use;
- sex-, age-, body-size-, rhythm-, loading-, ventilation-, and method-specific conditions;
- whether the cutoff diagnoses disease, grades severity, signals abnormality, or merely supports a multiparametric assessment;
- whether a single field is insufficient without other measurements;
- evidence tier and complete citation.

At minimum, address:

- LVEF <40% as the preserved historical primary endpoint;
- LVEF <50% only as a possible explicitly secondary sensitivity;
- any guideline-supported LVEF severity bands relevant to interpretation;
- AV peak velocity and aortic-stenosis grading, with multiparametric caveats;
- TR peak velocity/gradient and pulmonary-hypertension probability, with RAP and other-sign caveats;
- TAPSE, RV size, and IVC/RAP criteria;
- septal/lateral e′, E/A, E/e′, and diastolic-function algorithms;
- LV linear dimensions and wall thickness, including sex/body-size indexing;
- LA, RA, aortic sinus/root, ascending-aorta, and arch dimensions, including indexing and anatomic-level specificity;
- LVOT VTI and diameter, including whether standalone universal thresholds are appropriate;
- blood pressure and heart-rate thresholds only if their timing and context support interpretation.

Do not force a threshold for a continuous measurement when guidelines require a multiparametric algorithm or when definitions are ambiguous. Use `NONE_SUPPORTED` or `UNRESOLVED` as appropriate.

## Measurement-variability and margin questions

For `lvef` and every exact `legacy29` target, search for the strongest endpoint-specific evidence on:

- interobserver and intraobserver variability;
- test-retest or acquisition-reacquisition variability;
- repeatability coefficient or limits of agreement;
- method/vendor/view dependence;
- beat-to-beat and loading-condition sensitivity;
- clinically meaningful change, where such a construct has actually been validated.

Then determine whether the evidence can support:

- a native-unit absolute-error tolerance for report-label completion;
- a model-comparison equivalence/tie margin;
- a threshold uncertainty band;
- or no defensible prespecified margin.

State the numerical value, unit, direction, population, measurement method, and rationale. When transferring a published variability estimate into a proposed model-evaluation margin requires expert judgment, label the result `EXPERT_INFERENCE` and provide a sensitivity range rather than false precision. Where raw project units or definitions are unresolved, make the proposed value conditional rather than treating it as final.

## Evidence hierarchy

For every substantive recommendation, assign the strongest applicable tier:

1. `PROFESSIONAL_GUIDELINE_OR_CONSENSUS`
2. `PEER_REVIEWED_MEASUREMENT_METHODOLOGY`
3. `PEER_REVIEWED_CLINICAL_OBSERVATIONAL`
4. `FORMULA_BASED_INFERENCE`
5. `EXPERT_INFERENCE`
6. `UNRESOLVED`

Prefer current professional society guidelines/standards, but use the authoritative measurement-methodology source when it is older. Provide complete citations, year, journal/organization, DOI and PMID where available, and a direct link. Identify the exact claim supported by each citation. If studies disagree, report the disagreement, populations, and methods rather than averaging incompatible numbers.

Absence of literature found is not proof that no threshold or margin exists. Every unsupported recommendation must remain unresolved. Do not upgrade observational association to guideline authority.

## Required response format

Return all five components below.

### 1. Executive clinical interpretation

In no more than 800 words, identify the tasks with the clearest clinical report-completion rationale, tasks that are mainly formula/metadata reconstruction, and tasks whose definitions or units block interpretation.

### 2. LVEF endpoint ruling

Give a focused ruling on:

- preservation of LVEF <40% as the historical primary binary endpoint;
- whether LVEF <50% is defensible only as an explicitly secondary sensitivity;
- continuous-LVEF error tolerance and measurement variability;
- any near-threshold uncertainty band;
- limitations of using report labels as reference truth.

Separate guideline classification from project-design fidelity.

### 3. Machine-readable task adjudication

Return a UTF-8 CSV code block with one row for `lvef` and one row for every exact `legacy29` task, using exactly these columns:

```text
target,clinical_display_name,definition,standard_unit,required_method_or_view,provisional_family,recommended_family,cv_imaging_relevance,critical_care_relevance,perioperative_relevance,hemodynamic_monitoring_relevance,use_case_classification,scoring_disposition,reference_variability_value,reference_variability_unit,variability_construct,clinically_meaningful_absolute_error_margin,absolute_error_margin_unit,model_equivalence_tie_margin,equivalence_margin_scale,threshold_uncertainty_band,margin_evidence_tier,margin_certainty,conditional_assumptions,unresolved_metadata
```

Use these controlled values:

- relevance fields: `Direct`, `Contextual`, `Limited`, `None_supported`, or `Unresolved`;
- `use_case_classification`: `Plausible_report_completion`, `Formula_reconstruction`, `Metadata_reconstruction`, `Artificial_benchmark`, or `Unresolved`;
- `scoring_disposition`: `Score`, `Formula-only`, `Merge`, `Context-only`, `Remove`, or `Unresolved`;
- certainty: `High`, `Moderate`, `Low`, or `Unresolved`.

Use `NA` when a margin does not apply and `UNRESOLVED` when it might apply but evidence or metadata are insufficient. Do not silently substitute a typical unit for an unknown project unit.

### 4. Machine-readable threshold registry

Return a second UTF-8 CSV code block with zero, one, or multiple rows per target, using exactly these columns:

```text
target,threshold_label,operator,value,unit,intended_interpretation,primary_or_secondary_for_this_project,population,method_view_conditions,indexing_conditions,multiparametric_caveat,evidence_tier,citation_short,doi,pmid,certainty,reason_if_not_supported
```

Include explicit rows for LVEF <40% and LVEF <50%. For a target with no defensible standalone threshold, include one row with `threshold_label` set to `NONE_SUPPORTED` or `UNRESOLVED` and explain why.

### 5. Evidence, sensitivity ranges, and lock status

Provide:

- complete references with DOI/PMID where available, mapped to the relevant CSV rows;
- a compact family-level table of supported variability ranges and conditional margin ranges;
- sensitivity ranges for every margin based partly on expert inference;
- an explicit list of thresholds/margins that could be prespecified now;
- an explicit list that must remain unresolved pending raw descriptions, units, acquisition context, or clinician review.

Do not return a final locked task panel or use historical model results to resolve any uncertainty. We will combine this evidence with independent clinician adjudication before locking panels, thresholds, or margins and before any confirmatory test access.
