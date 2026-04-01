# Fullscale Output Audit

- Root: `/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all`
- Missing required files: `0`
- Warnings: `0`

## Cohort Counts

- Eligible studies: `4530`
- Keyframe stub studies: `4696`
- LVEF eval studies: `2833`
- Study embeddings studies: `4696`
- Structured measurement rows: `669378`
- Structured parsed-result rows: `145653`
- Structured unique measurement names: `186`

## Intersections

- LVEF ∩ study embeddings: `2833`
- LVEF missing in study embeddings: `0`
- LVEF ∩ keyframe stub: `2833`

## Eval Metrics

| Experiment | Config | Test n | Test AUC | Test R2 | Test MAE |
|---|---:|---:|---:|---:|---:|
| E2b_vision | - | 426 | 0.9581 | 0.6127 | 5.6761 |
| E3_tabular | - | 426 | 0.9153 | 0.4383 | 6.6842 |
| E5_fusion | fusion_concat__linear | 426 | 0.9618 | 0.6615 | 5.3935 |
