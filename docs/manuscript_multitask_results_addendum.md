# Multitask Expansion Results (Draft Addendum)

## Scope

This addendum summarizes the structured-measurement multitask expansion beyond single-endpoint LVEF prediction.

Pipeline anchors:
- fullscale cohort: 4,530 studies
- registry-derived strict task panel: 29 tasks
- modality comparisons: vision-only, tabular-only (leave-one-task-out), fusion (vision + leave-one-task-out tabular)

## Registry and Task Panel Construction

Structured measurements (669,378 rows; 186 raw measurement names) were canonicalized into 178 grouped tasks, with support and unit diagnostics computed per task. Applying support thresholds (minimum studies with numeric values, minimum numeric rows, minimum result-rate) yielded 38 tasks in a broad panel and 29 tasks in a strict panel after removing unknown preferred-unit targets.

Both multitask panels retained complete split mapping across 4,530 studies with zero missing split assignments.

## Multitask Strict-Panel Results (29 tasks)

Macro test metrics:
- Vision-only: mean R2 0.3196, median R2 0.3579, mean normalized MAE 0.4953
- Tabular-only: mean R2 0.4385, median R2 0.3920, mean normalized MAE 0.4191
- Fusion: mean R2 0.4886, median R2 0.5576, mean normalized MAE 0.3973

Interpretation:
- Tabular-only outperformed vision-only on macro multitask regression.
- Fusion provided the best overall macro performance, improving both R2 and normalized MAE over tabular-only and vision-only.

Illustrative high-performing tasks (fusion or strong unimodal performance) included:
- `task__fs`
- `task__tr_mmhg`
- `task__tricuspid_regurgitant_peak_velocity`
- `task__left_ventricular_end_systolic_diameter`
- `task__left_ventricular_end_diastolic_diameter`

## Recommended Manuscript Positioning

1. Keep LVEF results as the primary clinical anchor for continuity with prior benchmarks.
2. Present multitask expansion as a breadth/utility result showing the model family generalizes to multiple quantitative echo targets.
3. Emphasize strict-panel findings as primary (known preferred units), with broad-panel as sensitivity analysis.
4. Report macro metrics and task-level distributions (not only top tasks) to avoid cherry-picking.

## Reproducible Asset Command

```bash
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
PY=.venv-echoprime/bin/python
$PY scripts/generate_multitask_results_assets.py \
  --fullscale-root outputs/cloud_cohorts/fullscale_all \
  --vision-dir eval_multitask_vision_strict \
  --tabular-dir eval_multitask_tabular_strict \
  --fusion-dir eval_multitask_fusion_strict \
  --output-dir outputs/cloud_cohorts/fullscale_all/reporting_multitask_assets
```

