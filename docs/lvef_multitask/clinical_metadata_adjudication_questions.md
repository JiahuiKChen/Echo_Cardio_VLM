# Restricted clinical metadata adjudication

Status: **required before panel or dependency lock**. This is a Git-safe question template; it contains no restricted raw names, descriptions, units, or values. Complete it only beside the SCC-only packet produced by `scripts/lvef_multitask_clinical_metadata.py`. Record the selected option, clinician initials, date, and the packet checksum. Do not paste the completed form into Git until a separate aggregate-safety review confirms that every restricted field has been removed.

The reviewer should use raw name, raw description, canonical mapping/source, native and normalized units, and encoded method/view/timing indicators. Patient values, identifiers, DICOM locators, and model results are neither required nor permitted.

## Required decisions

For each item, select exactly one option unless the question explicitly permits multiple selections.

### CMR-01 — `tr_mmhg`

What does every raw field mapped to `tr_mmhg` represent?

- [ ] A. Peak RV–RA/TR pressure gradient only, derived as `4v^2`.
- [ ] B. RVSP or PASP, incorporating estimated right-atrial pressure.
- [ ] C. More than one construct is mixed under this canonical mapping.
- [ ] D. Another pressure construct; specify it in the restricted record.
- [ ] E. Unresolved from available metadata.

If A is selected, confirm that velocity and pressure units are compatible. If B–E is selected, `tr_mmhg` remains excluded from formula-only and scored-panel decisions.

### CMR-02 — `mitral_e_velocity` versus `mv_peak_e`

- [ ] A. Exact duplicate exports of transmitral peak E velocity with the same view, timing, and unit.
- [ ] B. The same construct after a documented unit conversion only.
- [ ] C. Distinct acquisition/method/timing constructs.
- [ ] D. Mixed or partially overlapping aliases.
- [ ] E. Unresolved.

Merging is allowed only after A or B is documented for every contributing raw alias.

### CMR-03 — `inf_lat_thickness`

- [ ] A. End-diastolic LV posterior-wall thickness used in the standard linear LV-mass formula.
- [ ] B. End-diastolic inferolateral wall thickness from a distinct 2-D convention.
- [ ] C. A mixture of A and B.
- [ ] D. Another wall-thickness construct.
- [ ] E. Unresolved.

### CMR-04 — `la_dimen`

- [ ] A. Parasternal long-axis anteroposterior LA diameter at ventricular end-systole.
- [ ] B. Another named LA linear dimension or plane.
- [ ] C. A mixture of measurement planes/methods.
- [ ] D. Unresolved.

### CMR-05 — `arch_diam`

- [ ] A. Proximal arch diameter at a consistent named level.
- [ ] B. Transverse arch diameter at a consistent named level.
- [ ] C. Distal arch/isthmus diameter at a consistent named level.
- [ ] D. More than one arch level is mixed.
- [ ] E. The level is not encoded and remains unresolved.

### CMR-06 — `sinus_diam` and `ascending_aorta_diameter`

For each target, select one edge convention and one timing convention.

- Edge: [ ] leading-edge to leading-edge  [ ] inner-edge to inner-edge  [ ] mixed  [ ] unresolved
- Timing: [ ] end-diastole  [ ] systole  [ ] mixed  [ ] unresolved
- Units: [ ] mm  [ ] cm  [ ] mixed with valid conversion  [ ] unresolved

Also select one relationship:

- [ ] A. Distinct, consistently defined anatomic levels.
- [ ] B. Duplicate or overlapping exports.
- [ ] C. Mixed definitions.
- [ ] D. Unresolved.

### CMR-07 — `ivc_diam`

- [ ] A. End-expiratory maximal diameter with respiratory-collapse information separately available.
- [ ] B. Diameter without phase/collapse context.
- [ ] C. A mixture of respiratory phases or ventilation states.
- [ ] D. Another convention.
- [ ] E. Unresolved.

### CMR-08 — `lvef` method composition

Select all methods explicitly represented among raw aliases, then choose whether method is available per label.

- Methods: [ ] visual  [ ] biplane Simpson  [ ] single-plane Simpson  [ ] Teichholz/linear  [ ] 3-D  [ ] contrast-enhanced  [ ] other  [ ] unknown
- Per-label method: [ ] always encoded  [ ] sometimes encoded  [ ] never encoded  [ ] unresolved
- Canonical mapping: [ ] one harmonizable construct  [ ] mixed methods requiring stratification  [ ] unsafe mixture  [ ] unresolved

### CMR-09 — EF aliases and LV systolic near-target fields outside `legacy29`

Does restricted metadata contain any of the following? Select each as `present`, `absent after complete search`, or `unresolved` in the restricted record: alternate EF exports; LVEDV; LVESV; qualitative LV systolic-function grades; wall-motion summaries; Simpson/3-D outputs; stroke volume; cardiac output; cardiac index.

- [ ] A. Complete search performed and every present field classified.
- [ ] B. Search incomplete; LVEF feature eligibility remains blocked.

### CMR-10 — dimension and velocity units

For each raw alias contributing to a scored target, verify its unit rather than assuming from the canonical name.

- Dimensions: [ ] one unit per target  [ ] mixed but deterministically convertible  [ ] mixed/incompatible  [ ] unresolved
- Velocities: [ ] one unit per target  [ ] `m/s` and `cm/s` with deterministic conversion  [ ] mixed/incompatible  [ ] unresolved
- LVEF: [ ] percentage points  [ ] fraction 0–1  [ ] mixed with deterministic conversion  [ ] unresolved
- Pressures: [ ] mm Hg  [ ] another unit  [ ] mixed  [ ] unresolved

### CMR-11 — `body_surface_area`

- Formula: [ ] Mosteller  [ ] Du Bois  [ ] Haycock  [ ] another documented formula  [ ] mixed  [ ] unresolved
- Weight input represented in restricted registry: [ ] yes  [ ] no  [ ] unresolved
- Precomputed BSA raw field: [ ] yes  [ ] no  [ ] unresolved

Do not infer BSA from `height_cm` alone.

### CMR-12 — indexed measurements

- [ ] A. No target or candidate near-target field is indexed.
- [ ] B. Indexed fields exist and the unindexed numerator plus indexing denominator are both identified.
- [ ] C. Indexed and unindexed definitions are mixed under at least one canonical mapping.
- [ ] D. Indexing status remains unresolved.

For B or C, record whether indexing uses BSA, height, height exponent, or another denominator in the restricted record.

### CMR-13 — mitral ratios and tissue-Doppler units

- [ ] A. E/A or E/e-prime ratio fields are present and all component units/aliases are classified.
- [ ] B. A complete search found no such ratio fields.
- [ ] C. Search or units remain unresolved.

Confirm separately whether `sept_e_prime` and `lat_e_prime` are tissue-Doppler velocities and whether `mv_peak_e`/`mv_peak_a` are transmitral inflow velocities.

### CMR-14 — LVOT/aortic derived outputs

- [ ] A. Stroke volume, cardiac output/index, AV area, AV VTI, or Doppler velocity index fields are present and classified.
- [ ] B. A complete search found none of these derived outputs.
- [ ] C. Search or definitions remain unresolved.

Do not classify `av_pk_vel` as algebraically recoverable from `lvot_vti` or `lvot_diam` without an explicit formula path and all required fields.

### CMR-15 — LV mass and relative wall thickness

- [ ] A. LV mass/index or relative wall-thickness fields are present and all formula inputs/units are classified.
- [ ] B. A complete search found no such fields.
- [ ] C. Search, posterior-wall identity, timing, or units remain unresolved.

## Lock decision

- [ ] Every required raw alias, description, unit, method/view/timing convention, and candidate near-target search has been adjudicated.
- [ ] Unknown or ambiguous rows are explicitly blocked rather than silently mapped.
- [ ] No model result or performance statistic was used.
- [ ] A clinician and a technical reviewer signed the restricted record.

Until all four boxes are checked, `raw_alias_unit_review`, `clinical_dependency_registry`, and `task_panels` remain closed gates.
