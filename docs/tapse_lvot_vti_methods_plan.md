# TAPSE and LVOT VTI Phase 1 Methods Plan

## 1. Current Study Goal

The immediate goal is to determine whether TAPSE and LVOT VTI prediction from MIMIC-IV-ECHO is scientifically feasible and reproducible. Phase 1 is an audit layer only: denominator accounting, ECHOVIEW join validation, target definition, leakage controls, and split integrity. Modeling is deferred until these audits pass.

## 2. Clinical Relevance

TAPSE is a clinically interpretable marker of right ventricular longitudinal systolic function and is relevant in shock, pulmonary hypertension, right heart strain, and critical care echocardiography.

LVOT VTI is a stroke-distance measure used as a surrogate for forward flow and cardiac output trends. In critical care, it can support hemodynamic assessment and response-to-intervention monitoring.

## 3. Why Native View Labels Are Insufficient

MIMIC-IV-ECHO native metadata do not provide consistently reliable clip-level view labels for the target measurement planes. The prior project also documented that EchoPrime's released view classifier has domain-shift concerns on MIMIC-IV-ECHO, so EchoPrime view outputs should not be used as the scientific view-filtering source.

## 4. Denominator Accounting And View-Label Availability

Each target requires a funnel with separate denominators:

1. structured-measurement studies with numeric target,
2. TTE numeric-target studies where modality metadata permit TTE/stress/TEE filtering,
3. public DICOM-subset availability,
4. processed EchoPrime study-embedding availability,
5. processed EchoPrime clip-level availability,
6. ECHOVIEW view-label availability,
7. high-confidence ECHOVIEW view-policy availability at 0.70, 0.80, 0.90, and 0.95,
8. processed target studies without ECHOVIEW labels,
9. studies excluded only because ECHOVIEW is unavailable,
10. conservative ECHOVIEW-filtered N versus broader fullscale embedding-available N.

All rows should be reported at study and subject levels, with clip counts where clip manifests are available.

## 5. Different Denominators

Structured measurements, DICOM availability, EchoPrime processed embeddings, and ECHOVIEW view labels are distinct resources. A study may have a structured TAPSE or LVOT VTI value but no public DICOM, public DICOM but no processed embedding, processed embedding but no ECHOVIEW row, or ECHOVIEW rows that do not pass a candidate view threshold.

## 6. ECHOVIEW Is A Derived Subset

ECHOVIEW covers approximately 717 studies, far smaller than the full MIMIC-IV-ECHO structured-measurement universe and smaller than the public DICOM subset. Low ECHOVIEW-overlap N must not be interpreted as absence of A4C, A5C, or Doppler clips in the full DICOM corpus. It may simply reflect that ECHOVIEW is a limited derived view-classification subset.

## 7. ECHOVIEW As Probabilistic View Filter

ECHOVIEW probabilities are used to construct view-policy sensitivity cohorts. They are not treated as ground-truth measurement-plane labels. The join must be performed at clip level, before study aggregation, so target-view pooling and threshold sensitivity can be audited.

## 8. Thresholds Are Sensitivity Parameters

Thresholds 0.70, 0.80, 0.90, and 0.95 should be reported as sensitivity settings. No threshold should be selected because it produces favorable model results. Primary threshold choice, if any, must be made from feasibility and clinical plausibility before final model evaluation.

## 9. TAPSE View Policies

Primary TAPSE feasibility policy: A4C-family probabilities using `prob_a4c`, `prob_a4c_lvocc_s`, and `prob_a4c_laocc`.

Sensitivity policy: A4C-family plus RV inflow using the A4C-family columns plus `prob_rvinf`.

ECHOVIEW does not identify RV-focused A4C, M-mode, tricuspid annular M-mode, or TMAD clips. Reports must avoid claiming measurement-plane specificity unless later audits directly support it.

## 10. LVOT VTI View Policies

LVOT VTI is a pulsed-wave Doppler measurement, commonly acquired from an apical LVOT view such as A5C or apical long-axis. ECHOVIEW's `prob_other` class includes Doppler, IV contrast, and unclassifiable clips, so strict A5C-only filtering is not clinically defensible as the primary policy.

Primary LVOT VTI feasibility policy: A5C-or-other using `prob_a5c` or `prob_other`.

Sensitivity policies: A5C-only, other-only, and all-clips comparator when embeddings are available.

## 11. Broader Embedding Cohort Versus ECHOVIEW Cohort

The broader fullscale embedding-available cohort may support all-clips or weakly view-aware baselines in later phases. The conservative ECHOVIEW-filtered cohort is useful for view-sensitive sensitivity analysis, validation, and calibration, but should not be assumed to be the only possible modeling cohort.

## 12. Leakage Risks And Mitigation

TAPSE leakage exclusions include TAPSE direct fields and synonyms, RV systolic function summaries, RV function fields, S prime/S' tissue Doppler fields, and RV fractional area change.

LVOT VTI leakage exclusions include LVOT VTI direct fields and synonyms, AV/aortic valve VTI, stroke volume, cardiac output, cardiac index, LVOT stroke distance, and direct derivatives.

Structured measurement comparators must be leave-target-and-direct-derivative-out. Any deliberate leakage/comparator experiment must be labeled as such and kept out of the primary imaging-only claim.

## 13. Required Audit Artifacts Before Modeling

- ECHOVIEW join coverage and duplicate audit.
- Denominator funnel for TAPSE and LVOT VTI.
- Target dictionary with raw names, canonical names, units, ranges, and leakage-adjacent fields.
- Target distribution summaries, duplicate-per-study counts, and implausible values.
- Threshold feasibility table by target, policy, and threshold.
- Embedding overlap table.
- Split integrity table.
- Target-specific leakage report.
- Warnings JSON with missing inputs and SCC run commands when needed.

## 14. Modeling Deferred

No TAPSE or LVOT VTI model should be trained until Phase 1 audit outputs exist, have been reviewed, and have no unresolved governance, denominator, join, leakage, or split-integrity blockers.

## 15. Reviewer-Risk Table

| Risk | Target | Severity | Mitigation |
|---|---|---:|---|
| ECHOVIEW subset mistaken for full DICOM denominator | Both | High | Report separate denominators and broader embedding-available N |
| View threshold treated as ground truth | Both | High | Report 0.70/0.80/0.90/0.95 sensitivity |
| A4C proxy overstated as RV-focused/M-mode TAPSE view | TAPSE | Medium | Use cautious wording and sensitivity policy |
| A5C-only filter excludes Doppler clips | LVOT VTI | High | Use A5C-or-other primary feasibility policy |
| Structured feature leakage | Both | High | Run target-specific leakage audit before any tabular/fusion comparator |
| Subject leakage | Both | High | Require deterministic subject-level split integrity checks |
| Patient-level outputs committed | Both | High | Keep patient-level files in approved restricted paths only |

## 16. Go/No-Go Criteria For Baseline Modeling

Proceed only if:

- target definitions and unit/range rules are frozen,
- denominator funnels show adequate target and embedding overlap,
- ECHOVIEW join audit has no unresolved many-to-many or study-mismatch issue,
- target-view threshold tables are generated and interpreted as sensitivity,
- target-specific leakage reports are reviewed,
- split integrity checks pass with no subject crossing splits,
- all patient-level outputs remain outside the repo,
- the planned primary analysis cohort is named before model evaluation.

Defer or reject a target if the target distribution is implausible, target support is too small for interpretable confidence intervals, view policy is clinically indefensible, or leakage cannot be controlled.
