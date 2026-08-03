# OpenEvidence dependency and leakage review

Status: **clinically informed technical draft; not a clinician-signed registry and not authorized for modeling**.

This review reconciles the complete dependency/leakage response preserved under `evidence_inputs/` with exact repository identifiers, verified primary-source claims, and the unresolved need for project-specific raw metadata. The draft relationship rows are in `target_dependency_registry_clinical_draft.csv`; claim-level provenance is in `clinical_claim_evidence_matrix.csv`.

## Input-quality audit

The OpenEvidence prose is useful as a hypothesis generator, but its machine-readable sections are not ingestible:

- underscores in canonical identifiers were replaced by asterisks;
- citation markers entered identifiers, sentences, and field values;
- the relationship CSV is truncated after the first few `lvef` rows;
- the LVEF ruling and task-adjudication CSV are malformed/truncated;
- figures and reference text interrupt CSV rows;
- some formula, margin, and family recommendations are explicitly low-certainty expert inference;
- a cited paper cannot establish the meaning of a project-specific raw export.

No OpenEvidence CSV row was imported. Asterisks were not automatically repaired. Every target or predictor in the draft registry was entered from this hard allowlist only:

```text
lvef
body_surface_area
resting_sbp
resting_dbp
resting_hr
left_ventricular_end_diastolic_diameter
septal_thickness
inf_lat_thickness
la_dimen
sinus_diam
mv_peak_e
mitral_e_velocity
av_pk_vel
la_4ch_length
ra_length
ascending_aorta_diameter
lvot_diam
rv_diam
lvot_vti
mv_peak_a
tr_mmhg
tricuspid_regurgitant_peak_velocity
left_ventricular_end_systolic_diameter
lat_e_prime
sept_e_prime
arch_diam
fs
ivc_diam
height_cm
tricuspid_annular_plane_systolic_excursion
```

Unknown candidate concepts such as LVEDV, LVESV, qualitative LV function, wall motion, ratios, stroke volume, cardiac output/index, valve area, LV mass/index, IVC collapse, or weight are **restricted metadata search concepts**, not asserted repository fields and not valid predictor identifiers until the source mapping proves their existence.

## Evidence verification

Three primary-source claims were independently checked against official sources:

1. ASE/EACVI 2015 supports volumetric EF as `(EDV-ESV)/EDV`, recommends biplane disk summation when 3-D is unavailable, and cautions against inaccurate linear/Teichholz volume estimates. This supports the formula and the need to distinguish methods; it does not identify the project `lvef` method.
2. ASE 2025 states `RVSP = 4 × peak TR velocity^2 + RAP` and distinguishes the RV-RA gradient component. This supports the conditional formula; it does not prove what `tr_mmhg` stores.
3. The ACC summary of the 2022 AHA/ACC/HFSA guideline supports the `<=40`, `41-49`, and `>=50` heart-failure EF categories. It does not change the accepted-abstract endpoint `lvef < 40`.

The locally supplied Jozwiak et al. 2019 full text verifies VTI repeatability context in 100 stable ICU patients: median least significant change was 11% for repeated examinations by the same operator and 14% with different operators. That relative change result is not a native-unit report-label error margin or model equivalence threshold.

All other OpenEvidence-attached citations not present as supplied full text remain `UNVERIFIED_OPENEVIDENCE_CITATION`. They can support a future source-retrieval queue but cannot upgrade a draft row. Exact DOI strings are retained only in the claim matrix when a specific claim was checked or clearly attributable; citation attachment alone is not treated as verification.

## Relationship separation

### Exact aliases and duplicates

- Every target has a confirmed direct self-exclusion.
- All raw aliases of each target remain unknown pending the SCC-only packet.
- `mv_peak_e` and `mitral_e_velocity` are a suspected duplicate pair, not a confirmed merge. Both fail closed as predictors for the other until descriptions, methods, timings, and units agree for all contributing raw aliases.
- Candidate EF aliases outside the hard allowlist must be discovered from the restricted source mapping. No candidate pattern establishes field existence.

### Deterministic formula sets

- `fs`, `left_ventricular_end_diastolic_diameter`, and `left_ventricular_end_systolic_diameter` form a conditional deterministic triad only if `fs` uses the standard compatible-plane formula. `fs` is provisionally formula-only; raw definition and units remain a gate.
- `tr_mmhg` and `tricuspid_regurgitant_peak_velocity` form a conditional transform only if `tr_mmhg` is the peak RV-RA/TR gradient. If it is RVSP/PASP, estimated RAP is additionally required. `tr_mmhg` therefore remains unresolved rather than automatically formula-only.
- LVEDV/LVESV-to-LVEF, mitral ratios, LVOT flow outputs, LV mass/index, and indexing formulas are candidate dependency sets outside the current allowlist. They must be sought in the restricted registry before feature eligibility can be locked.

### Near-deterministic clinical derivatives

- Volumetric LVEF is not algebraically determined by `fs`, LVEDD, or LVESD. Their treatment depends on whether project LVEF mixes visual, biplane, Teichholz/linear, 3-D, or other methods. These predictors fail closed for LVEF until method metadata are reviewed.
- Qualitative LV function and alternate EF exports, if present, would be high-risk near-target or duplicate fields. Their existence is unproven.
- Indexed fields, if present alongside unindexed numerators and BSA/height, can create direct reconstruction pathways. Indexing status is unproven.

### Clinical correlation families

Clinical correlation is not leakage by itself and does not create a formula edge. The provisional broad families are split as follows:

| Domain | Formula dependency set | Clinical correlation set | Current conclusion |
|---|---|---|---|
| LV systolic | FS linear triad; volumetric EDV/ESV/EF if fields exist | volumetric LVEF versus linear geometry | Do not call linear dimensions deterministic LVEF proxies without method evidence. |
| TR/pulmonary pressure | peak TR velocity to gradient; gradient plus RAP to RVSP | IVC/venous context and broader RV/pulmonary findings | `ivc_diam` alone is not RAP and does not reconstruct TR velocity. |
| Mitral diastolic | E/A and E/e-prime only if ratio fields exist | inflow E/A and septal/lateral tissue-Doppler velocities | Distinct measurement sites; family-mask may remove all, pragmatic may retain nonalgebraic context. |
| LVOT/aortic | LVOT diameter/area/VTI to stroke volume and downstream CO/CI if outputs exist | AV peak velocity and subvalvular LVOT flow | `av_pk_vel` is not algebraically recovered from `lvot_vti` or `lvot_diam`. |
| LV wall geometry | LV mass/RWT formulas if relevant outputs and definitions exist | wall thicknesses and LV diastolic geometry | `inf_lat_thickness` identity blocks the formula classification. |
| Atria/aorta | indexing formulas if indexed outputs exist | different chambers, views, and anatomic aortic levels | Do not merge or treat dimensions as interchangeable. |
| RV | no supplied deterministic relation among `rv_diam` and TAPSE | structure versus longitudinal function | Split from the TR-pressure and IVC/RAP constructs. |

The family-masked construct may remove a clinically coherent correlation family. The strict construct removes only exact/alias/formula/near-deterministic or otherwise adjudicated shortcut fields; the pragmatic construct may retain nonalgebraic clinical context. These differences concern **predictor eligibility**, not selection of different scored targets.

### Context-only and unrelated predictors

`body_surface_area`, `height_cm`, `resting_sbp`, `resting_dbp`, and `resting_hr` are provisionally context/metadata targets and do not enter the primary echo-measurement macro under any construct. Pragmatic completion may use a context variable as a predictor when it is neither an alias nor a deterministic/near-deterministic reconstruction path for the target. BSA/height must fail closed for potentially indexed targets until indexing is reviewed.

Cross-family predictors absent from the draft registry are not automatically approved. The production registry must expand against the full restricted predictor set, apply alias and formula searches, and document an `INDEPENDENT_STRUCTURED_PREDICTOR` decision or remain unresolved. Unknown relationships fail closed.

## Draft decisions

Can be locked now:

- exact target self-exclusion precedes imputation and scaling;
- only exact repository identifiers are accepted;
- formula sets and clinical correlation sets are distinct;
- LVEF remains a separate anchor;
- accepted-abstract `lvef < 40` remains historical primary;
- no raw field is inferred to exist from an OpenEvidence pattern;
- no broad family is declared a deterministic set merely because its members correlate clinically.

Must remain provisional:

- complete raw aliases for all 30 allowlisted targets;
- LVEF method mixture and EF-adjacent fields;
- the meaning of `tr_mmhg`;
- whether `mitral_e_velocity` duplicates `mv_peak_e`;
- the meaning of `inf_lat_thickness`, `la_dimen`, `arch_diam`, and `ivc_diam`;
- dimension/velocity/pressure units and conversions;
- BSA formula/weight availability and all indexed fields;
- final family-mask memberships;
- all predictor-allowed fields in a production-complete registry.

The SCC metadata packet and dual clinician/technical signoff are therefore required before the draft registry can replace the current provisional dependency registry.
