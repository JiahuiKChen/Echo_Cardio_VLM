# Denominator funnel

## Interpretation rule

Counts below are separated by authority. “Phase 1A proven” means the restricted source was inspected and only aggregate counts/equality flags were exported. “Historical” means the count is preserved from the accepted project record but the corresponding source artifact was not supplied to the Phase 1A artifact audit. All modality comparisons require exact subject-study-target identity; equal counts alone are insufficient.

## Imaging and structured-data funnel

| Stage | All-artifact studies | Selected studies | Selected subjects | Status and authority |
|---|---:|---:|---:|---|
| Public DICOM corpus | 7,243 | — | 4,579 | Historical public-metadata count; source not supplied to Phase 1A |
| Measurement-linked and at least five DICOMs | 7,104 | — | 4,530 | Historical aggregate recomputation; eligible artifact not supplied to Phase 1A |
| Removed by one-study-per-subject policy | 2,574 | — | 0 additional | Historical arithmetic from 7,104 to 4,530 |
| Selected one-study-per-subject cohort | 4,530 | 4,530 | 4,530 | Phase 1A proven; one row/study/subject, no duplicates or ownership mismatches |
| Prior Stage-D manifest | 500 | 329 | 329 | Phase 1A proven; 171 prior-stage studies are outside the selected cohort |
| Nine-batch selected remainder | 4,201 | 4,201 | 4,201 | Phase 1A proven; exact partition, no duplicate assignment |
| Downloaded, at least one successful row | 4,701 | 4,530 | 4,530 | Phase 1A proven; includes the 171 outside-selected Stage-D studies |
| Readable DICOM, at least one successful row | 4,701 | 4,530 | 4,530 | Phase 1A proven |
| At least one multiframe/cine candidate | 4,696 | 4,525 | 4,525 | Phase 1A proven; five selected studies first disappear here |
| At least one extracted cine | 4,696 | 4,525 | 4,525 | Phase 1A proven; no loss after cine candidacy |
| At least one clip embedding | 4,696 | 4,525 | 4,525 | Phase 1A proven; 191,993 clip rows |
| Study embedding | 4,696 | 4,525 | 4,525 | Phase 1A proven; `4,696 x 512` store |
| Structured-measurement export | 4,530 | 4,530 | 4,530 | Phase 1A proven; 669,378 rows |
| Historical 29-task wide panel | 4,530 | 4,530 | 4,530 | Phase 1A proven as a base table; task labels are available-case |

The 4,696-study embedding store is not a one-study-per-subject selected-cohort store. It consists of 4,525 selected studies plus 171 prior-stage studies outside selection. Its 4,525 subjects reflect the selected embedded cohort; the outside-selected studies are repeat/alternate studies of those historical subjects rather than additional selected subjects.

## Selected-study attrition

| Loss category | n selected studies | Current interpretation |
|---|---:|---|
| Download failure | 0 | Ruled out at study level |
| No readable DICOM | 0 | Ruled out at study level |
| No recorded cine candidate | 5 | Proven stage of loss; root cause unresolved |
| Cine extraction failure after candidacy | 0 | Ruled out at study level |
| Clip-embedding failure after extraction | 0 | Ruled out at study level |
| Study-aggregation failure after clip embedding | 0 | Ruled out at study level |
| Indeterminate downstream loss | 0 | No later attrition |

The five cannot yet be labeled a principled exclusion. Restricted aggregate diagnostics must distinguish a true lack of usable multiframe cine from manifest or processing omission. Three of the five have numeric LVEF labels. If no usable cine exists under a prespecified imaging-usability definition, exclude them from all imaging comparisons and report the attrition; if processing was incomplete, deterministically reprocess before cohort lock. A sensitivity analysis may compare the selected-label cohort with and without the imaging-usability restriction, but cannot manufacture an imaging prediction for a study without usable input.

## LVEF funnel

| Stage | All | Train | Validation | Test | Status |
|---|---:|---:|---:|---:|---|
| Selected cohort | 4,530 | 3,171 | 679 | 680 | Phase 1A split-map authority; zero duplicate/overlap/invalid split rows |
| Selected numeric LVEF before imaging | 2,836 | Not exported | Not exported | Not exported | Phase 1A derived from exact raw name `lvef`, numeric median by subject/measurement ID |
| Numeric LVEF plus study embedding | 2,833 | 1,997 | 410 | 426 | Phase 1A proven |
| Historical LVEF manifest | 2,833 | 1,997 | 410 | 426 | Phase 1A proven; values agree with the structured derivation on the intersection |
| Vision-only historical predictions | 2,833 | 1,997 | 410 | 426 | Exact IDs/labels agree with the LVEF manifest |
| Structured-only historical predictions | 2,833 | 1,997 | 410 | 426 | Exact IDs/labels agree with the LVEF manifest |
| Early-fusion historical predictions | Not preserved | Not preserved | Not preserved | Not preserved | Three-way paired historical inference blocked |

The historical statement “LVEF missing embeddings = 0” applies to a manifest constructed after image linkage. It does not describe the pre-imaging label funnel: 2,836 selected studies have numeric LVEF, and three are lost at the imaging intersection.

## Historical 29-task available-case denominators

The wide/long panel authorities contain the same 29 target definitions, without blank or duplicate task definitions. Label availability varies markedly by task.

| Denominator type | Train range | Validation range | Test range | All-study range |
|---|---:|---:|---:|---:|
| Structured/panel available labels | 789–3,156 | 184–673 | 161–675 | 1,134–4,504 |
| Common vision/fusion denominator | 787–3,153 | 184–672 | 160–674 | 1,131–4,499 |

The lower end is TAPSE; high-availability tasks include body surface area and routine vitals. The denominator range is not itself a reason to retain or remove a task. Clinical scope, units, dependency status, and prespecified minimum support must be locked without using model performance.

## Multitask modality discrepancy

Vision-only and early-fusion prediction tables are identical at the cohort level: 3,166 train, 678 validation, and 678 test subjects/studies. Structured-only contains five additional selected subjects/studies overall: 3,169 train, 679 validation, and 679 test. Per task, zero to five of these have observed labels.

On the common rows:

- all continuous labels agree;
- subject-study ownership agrees;
- split assignment agrees;
- no missing pair IDs, duplicate prediction keys, multi-subject studies, or within-subject split conflicts were found;
- wide and long panel authorities agree on target names and observed labels.

The common-denominator audit nevertheless records 344 identity failures because panel/structured sources include up to five imaging-ineligible studies that vision/fusion omit across target, split, and identifier-level comparisons. At test, only six tasks already have exact equality across all sources (`arch_diam`, `fs`, `height_cm`, `ivc_diam`, `left_ventricular_end_systolic_diameter`, and `mv_peak_a`); the remaining 23 have one structured/panel study outside the common set. This is an input-availability denominator difference, not a continuous-label or ownership disagreement.

Historical cross-modality multitask summaries therefore cannot be interpreted as paired incremental-value estimates. Confirmatory revalidation must construct the observed-target-plus-embedding common cohort first and feed the same ordered subject-study-target rows to every modality.

## Comparator identity lock

For every target and split, the revalidation must export aggregate proof of:

1. identical subject sets;
2. identical study sets;
3. identical subject-study pairs and ownership;
4. identical split assignments;
5. identical continuous target values and, when applicable, binary labels;
6. no duplicate prediction key;
7. the denominator and reason for every exclusion.

Any failure is fatal before fitting. No identifier list, row-level label/prediction table, or hash-to-identifier lookup may enter Git.
