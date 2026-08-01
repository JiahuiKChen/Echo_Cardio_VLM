# Denominator funnel

## Historical and currently established counts

| Stage | Studies | Subjects | Status |
|---|---:|---:|---|
| Public DICOM corpus | 7,243 | 4,579 | Established from public metadata |
| Measurement-linked and at least five DICOMs | 7,104 | 4,530 | Aggregate recomputation; verify against frozen SCC manifest |
| Repeat eligible studies removed by primary policy | 2,574 | 0 unique | Aggregate recomputation |
| One-study-per-subject selected cohort | 4,530 | 4,530 | Historical authority |
| Successfully downloaded | Unknown | Unknown | SCC audit required |
| At least one readable DICOM | Unknown | Unknown | SCC audit required |
| At least one extracted cine | Unknown | Unknown | SCC audit required |
| At least one clip embedding | Unknown | Unknown | SCC audit required |
| Selected studies with study embedding | Apparently 4,525 | At most 4,525 | Exact ID audit required |
| Combined study-embedding store | 4,696 | 4,525 | Historical aggregate authority; mixed lineage |
| Combined clip embeddings | 191,993 | — | Historical aggregate authority |
| Structured-measurement export | 4,530 | 4,530 | 669,378 rows |
| Numeric-parsed structured rows | — | — | 145,653 rows |
| Numeric LVEF before image linkage | Unknown | Unknown | Must be reconstructed before confirmatory analysis |
| LVEF plus embedding | 2,833 | 2,833 | Historical linked cohort |
| LVEF train/validation/test | Unknown / Unknown / 426 | Same | Restricted split audit required |
| Legacy multitask base panel | 4,530 | 4,530 | Task-specific missingness |
| Historical task-specific test support | 160–675 structured; 160–674 vision/fusion | Task-specific | 23 of 29 comparisons differ |

## Required loss accounting

The SCC audit must classify losses at each transition without inferring a reason from absence alone:

- public metadata to eligibility;
- eligibility to one-study selection;
- selection to download completion;
- download to readable DICOM;
- readable DICOM to extracted cine;
- extracted cine to clip embedding;
- clip embedding to study embedding;
- selected cohort to numeric LVEF before image linkage;
- numeric LVEF to common modality cohort;
- base panel to each task's train/validation/test common denominator.

The historical `LVEF missing embeddings = 0` check is not a pre-image denominator check because the LVEF manifest was constructed after image linkage.

## Comparator identity rule

For each target and split, vision-only, structured-only, and early fusion must use identical study IDs, subject IDs, and target values. Equal counts are necessary but insufficient. The audit must compare sorted identifier sets and cryptographic hashes of restricted ID lists.

No identifier list or hash-to-ID lookup table may be committed to Git.
