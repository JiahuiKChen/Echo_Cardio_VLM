# Provisional target and predictor-mask constructs

Status: **candidate-only; no final panel is frozen**.

Historical `legacy29` scored all 29 targets. The Phase 1C proposal separates target disposition from predictor masking. The same 21 provisionally scoreable echo measurements are candidates under strict, target-family-masked, and pragmatic analyses; the constructs differ in which structured predictors are permitted. Historical performance was not used.

## Three analysis constructs

| Construct | Candidate scored targets | Predictor exclusion rule | Context predictors |
|---|---:|---|---|
| Strict leakage-minimized measurement | Same provisional echo-measurement 21 | Exclude exact target, every alias/duplicate, deterministic and near-deterministic ancestors/descendants, method-dependent shortcut fields that remain unresolved, and any indexing/formula pathway established by the reviewed registry. | May be retained only after being classified independent for that target; BSA/height fail closed for potentially indexed targets. |
| Target-family-masked report completion | Same provisional echo-measurement 21 | Apply the strict exclusions, then mask the clinically coherent correlation family prespecified for that target. Formula dependency sets and broad clinical families remain separately identified. | Remove when inside the prespecified target family; otherwise follow the strict independence review. |
| Pragmatic same-report completion | Same provisional echo-measurement 21 | Exclude exact target, aliases/duplicates, deterministic formulas, near-deterministic reconstruction, and unresolved high-risk mappings. | Permit clinically plausible nonalgebraic same-report context after target-specific review. |

The five context/metadata targets never enter the primary echo-measurement macro, including in the pragmatic construct. A separate context benchmark may be reported, but cannot be pooled with the primary macro or presented as image measurement automation.

## Before/after target table

`Candidate score` means eligible for restricted metadata and clinician review, not frozen inclusion.

| Exact target | Historical role | Phase 1C target disposition | Strict target | Family-masked target | Pragmatic target | Reason | Evidence tier / unresolved gate |
|---|---|---|---|---|---|---|---|
| `lvef` | Separate historical anchor, not `legacy29` | Separate continuous and binary anchor | Separate | Separate | Separate | Primary scientific anchor; do not dilute it into a heterogeneous macro. | Direct repository authority for endpoint history; method/alias review unresolved. |
| `body_surface_area` | Scored in `legacy29` | Context-only; optional separate metadata benchmark | No | No | No | Formula/metadata quantity, not an acquired echo measurement. | Formula inference; BSA formula and weight availability unresolved. |
| `resting_sbp` | Scored in `legacy29` | Context-only; optional separate metadata benchmark | No | No | No | Contemporaneous hemodynamic context, not a video-derived measurement. | Expert inference; source/timing unresolved. |
| `resting_dbp` | Scored in `legacy29` | Context-only; optional separate metadata benchmark | No | No | No | Contemporaneous hemodynamic context, not a video-derived measurement. | Expert inference; source/timing unresolved. |
| `resting_hr` | Scored in `legacy29` | Context-only; optional separate metadata benchmark | No | No | No | ECG/report metadata; cadence recovery is not the same as echo measurement. | Expert inference; source/rhythm/timing unresolved. |
| `left_ventricular_end_diastolic_diameter` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct linear echo measurement with a plausible label-completion use case. | Expert inference pending unit/method/indexing and LVEF-method review. |
| `septal_thickness` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct wall-thickness measurement. | Expert inference pending unit/method and LV-mass field search. |
| `inf_lat_thickness` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Potential direct wall measurement, but exact definition is unclear. | Unresolved pending CMR-03 and unit/method review. |
| `la_dimen` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct report label if anatomic plane is consistent. | Unresolved pending CMR-04; clinical meaning weaker than LA volume. |
| `sinus_diam` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct aortic-root-level measurement if level/convention is stable. | Unresolved pending CMR-06 and indexing search. |
| `mv_peak_e` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct transmitral Doppler label if method/unit are confirmed. | Expert inference pending CMR-02/10/13. |
| `mitral_e_velocity` | Scored in `legacy29` | Unresolved; merge into `mv_peak_e` only if equivalence is proven | No | No | No | Suspected duplicate could otherwise double-count one construct. | Unresolved pending CMR-02; no merge authorized. |
| `av_pk_vel` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct Doppler measurement with plausible report-completion relevance. | Expert inference pending unit/method review; not derivable from LVOT fields. |
| `la_4ch_length` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct view-specific linear measurement. | Expert inference pending view/unit review. |
| `ra_length` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct right-atrial linear measurement if axis/view are fixed. | Expert inference pending axis/view/unit review. |
| `ascending_aorta_diameter` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct vessel measurement if level/edge/timing are stable. | Unresolved pending CMR-06 and indexing search. |
| `lvot_diam` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct measurement with strong formula dependencies that can be masked. | Expert inference pending level/phase/unit and derived-output search. |
| `rv_diam` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct RV structural measurement if axis/view are consistent. | Expert inference pending axis/view/unit review. |
| `lvot_vti` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct Doppler trace label with perioperative/critical-care relevance. | Direct evidence supports relative repeatability context; project unit/rhythm/beat averaging unresolved. |
| `mv_peak_a` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct inflow measurement when an A wave is present. | Expert inference pending unit/rhythm and absent/fused-wave handling. |
| `tr_mmhg` | Scored in `legacy29` | Unresolved; formula-only only if peak gradient is confirmed | No | No | No | May be `4v^2`, RVSP/PASP, or another pressure export. | Formula verified in general; project identity unresolved pending CMR-01. |
| `tricuspid_regurgitant_peak_velocity` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct CW Doppler measurement; pressure formula pathway is maskable. | Expert inference pending unit/method/measurability review. |
| `left_ventricular_end_systolic_diameter` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct linear measurement with formula dependencies that can be masked. | Expert inference pending unit/method/indexing and LVEF-method review. |
| `lat_e_prime` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct lateral annular tissue-Doppler measurement. | Expert inference pending site/unit/method and ratio search. |
| `sept_e_prime` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct septal annular tissue-Doppler measurement. | Expert inference pending site/unit/method and ratio search. |
| `arch_diam` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Potential direct measurement, but arch level is not established. | Unresolved pending CMR-05 and unit/edge review. |
| `fs` | Scored in `legacy29` | Formula-only if standard definition is confirmed | No | No | No | Standard FS is determined by LVEDD/LVESD and would not be an independent ML endpoint. | Formula inference; raw definition/method/units unresolved. |
| `ivc_diam` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct diameter label, separate from RAP unless phase/collapse inputs are present. | Unresolved pending CMR-07 and ventilation/phase/unit review. |
| `height_cm` | Scored in `legacy29` | Context-only; optional separate metadata benchmark | No | No | No | Patient metadata, not an echo measurement. | Direct construct identity; source/unit and BSA linkage unresolved. |
| `tricuspid_annular_plane_systolic_excursion` | Scored in `legacy29` | Candidate score | Yes | Yes | Yes | Direct longitudinal RV-function measurement. | Expert inference pending unit/method review. |

## Provisional scoreable echo-measurement 21

```text
left_ventricular_end_diastolic_diameter
septal_thickness
inf_lat_thickness
la_dimen
sinus_diam
mv_peak_e
av_pk_vel
la_4ch_length
ra_length
ascending_aorta_diameter
lvot_diam
rv_diam
lvot_vti
mv_peak_a
tricuspid_regurgitant_peak_velocity
left_ventricular_end_systolic_diameter
lat_e_prime
sept_e_prime
arch_diam
ivc_diam
tricuspid_annular_plane_systolic_excursion
```

This list is deliberately called provisional rather than `strict21-v1`. It cannot be frozen until the SCC metadata packet, clinician adjudication, complete raw-alias search, production-complete dependency registry, unit lock, and target-specific predictor masks all pass.
