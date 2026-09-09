# Owner-relayed echocardiographer review — 2026-09-09

The owner relayed a qualified echocardiographer's agreement with the supplied
operational interpretations and explicitly authorized their incorporation. The
exact [owner communication](owner_relayed_review_source_2026_09_09.txt) has SHA-256
`c752a837fa9dc9defdf0ed0e91bf2ffaefca998eb6928f2e37020554e2b8109f`.
The communication was observed in this task at 2026-09-09 14:40:15 UTC. This is
the communication record time, not the unknown date of the expert's review.

The versioned route is `OWNER_RELAYED_QUALIFIED_ECHO_REVIEW`. Qualification and
agreement are reported by the owner; the expert's name, direct signature and
review date were not provided and remain null. The original questionnaire and
blank direct-entry response remain unchanged. The new evidence is separately
bound to that packet, its source metadata, the preserved numerical input receipt
and the exact owner statement. Its status distinguishes completed clinical
adjudication from a directly signed questionnaire. This fixed route accepts only
these supplied decisions and is not a general missing-review override.

## Interpretation and consequences

| Issue | Expert-endorsed operational interpretation | Bounded processing consequence |
|---|---|---|
| Arch diameter | Transverse arch | Retain one arch task; source-level location mixing remains possible |
| Ascending aorta | Tubular ascending aorta, leading-edge/end-diastolic convention | Use the existing `ascending_aorta_diameter`; retain its compatible numeric source and exclude two nonnumeric/unknown-unit exports |
| Inferolateral thickness | End-diastolic inferolateral wall; posterior wall is an accepted nomenclature synonym here | One construct and task; no merge with unrelated wall exports |
| IVC diameter | End-expiratory diameter alone | Do not derive collapse, ventilation status or RAP |
| LA dimension | PLAX anteroposterior linear dimension at LV end-systole | Do not treat this as LA volume or claim examination-level timing verification |
| Mitral E fields | Probable shared clinical construct, subject to an incompatible declared source unit | Retain `mv_peak_e`; exclude `mitral_e_velocity` from labels, aggregation and predictors; no merge or time-to-velocity conversion |
| Sinus diameter | Sinus of Valsalva, leading-edge/end-diastolic convention | Retain the field as an operational task, with source-adherence uncertainty |
| TR pressure field | Peak TR-derived RV–RA gradient without RAP | Do not relabel as RVSP/PASP or change recorded labels by adding/subtracting RAP; retain formula/family masks |

These are expert-endorsed, guideline-informed interpretations of terminology,
including their "most likely" qualification. They are not verification that each
source examination followed those conventions. Fourteen other direct candidate
measurements have project-metadata operational definitions and technical unit
review; they must not be falsely attributed to this eight-item expert review.
The exact-name LVEF label retains its separate operational authority, unverified
native unit declaration and unknown acquisition-method composition.

Q6 is recorded as `SAME_CONSTRUCT_HYPOTHESIS_SOURCE_UNIT_CONFLICT`, with separate
clinical-interpretation and processing fields. The valid E-velocity source has
supported velocity units. The other source is declared in milliseconds and stays
excluded even if the names refer to the same intended clinical construct. At
most one scored E-velocity construct is permitted; joint mitral-family masking
remains. No source repair, correlation-based identity claim or new aggregation
is authorized by this interpretation.

The proposed strict panel retains the 21 reviewed candidates because each has a
supported operational label, compatible numeric source, adequate support and
positive training IQR. LVEF remains a separate anchor. The final panel and exact
positive raw predictor decisions must be generated and replayed through the
maintained gates before fitting. The unchanged input arrays need no regeneration
for these interpretation/mask amendments. No clinical non-LVEF margin is invented;
unresolved margins suppress margin-based labels, not errors or intervals.

## Methods and limitations amendment

A qualified echocardiographer reviewed guideline-informed operational
interpretations for ambiguous structured measurement fields, with agreement
relayed by the project owner. Source-unit compatibility and technical processing
eligibility were assessed separately. Fields with incompatible units were
excluded from the relevant analyses rather than merged on the basis of similar
names. The direct signature, expert identity and expert review date were not
supplied; this provenance is recorded explicitly.

Several measurement locations, timings and conventions were inferred from field
terminology and standard echocardiographic practice rather than verified in
individual acquisition records. Residual variation may contribute to label
heterogeneity. In particular, the milliseconds-valued `mitral_e_velocity` export
was excluded while the independently supported `mv_peak_e` source was retained.
These report-label interpretations do not establish direct measurement accuracy.

## Primary guidance and unresolved citation markers

The owner's reference markers [1–16] were supplied without a bibliography. They
remain unresolved source citation identifiers. The following independently
verified primary documents support general conventions; they are not assigned
to those missing numbers and do not verify this dataset's acquisitions.

- **Lang et al., ASE/EACVI chamber quantification (2015).** Supports the adult
  end-diastolic leading-edge convention for nonannular aortic root/ascending
  measurements and the distinction between LA linear dimensions and LA volume.
  A single AP dimension is not a comprehensive measure of atrial size.
  [Primary guideline](https://www.asecho.org/wp-content/uploads/2025/04/2015_ChamberQuantificationREV.pdf).
- **Mitchell et al., ASE comprehensive adult TTE (2019).** Describes matched
  timing/level for LV end-diastolic diameter and septal/posterior wall thickness,
  and acquisition of the LA AP linear dimension. This supports operational
  measurement context, not a patient-level alias identity audit.
  [Primary guideline](https://www.asecho.org/wp-content/uploads/2025/04/2019_Comprehensive-TTE.pdf).
- **Mukherjee et al., ASE right-heart assessment (2025).** Separates end-expiratory
  IVC diameter from respiratory variation used to estimate RAP and distinguishes
  the TR-derived RV–RA gradient from RVSP, which also incorporates RAP. The
  [primary guideline](https://www.asecho.org/wp-content/uploads/2026/08/ASE-Right-Heart-Guidelines-May-2025.pdf)
  supersedes the 2010 guideline according to the
  [ASE publication record](https://www.asecho.org/guideline/right-heart-in-adults-pulmonary-hypertension/).

These writing references do not create an additional clinical gate. Statistical
execution remains governed by exact source/clinical/panel/input/environment
bindings and the existing owner authorization.
