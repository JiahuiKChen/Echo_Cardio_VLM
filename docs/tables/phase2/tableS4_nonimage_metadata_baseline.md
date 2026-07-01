# Supplementary Table S4. Leakage-Safe Non-Image Baselines

Non-image Ridge baselines used the same target-positive study denominators and deterministic subject-level splits as the imaging-only Phase 2 analyses.

| Target | Baseline | Test N | Predictors | Null MAE | Baseline MAE | Baseline MAE 95% CI | Baseline R2 | Baseline R2 95% CI | Imaging Ridge MAE | Imaging Ridge R2 | Selected alpha | Note |
|---|---|---:|---|---:|---:|---|---:|---|---:|---:|---:|---|
| LVOT VTI | Demographics-only Ridge | 555 | `age_at_echo_approx`, `sex` | 4.54 cm | 4.47 cm | 4.16 to 4.76 cm | 0.043 | 0.023 to 0.058 | 3.64 cm | 0.372 | 0.01 | Modest improvement over null; below imaging-only Ridge. |
| LVOT VTI | Study/acquisition metadata Ridge | 555 | `n_clips`, `n_dicoms` | 4.54 cm | 4.58 cm | 4.28 to 4.89 cm | 0.008 | -0.012 to 0.025 | 3.64 cm | 0.372 | 0.01 | Metadata-only baseline performed near null. |
| LVOT VTI | Demographics + study/acquisition metadata Ridge | 555 | `age_at_echo_approx`, `sex`, `n_clips`, `n_dicoms` | 4.54 cm | 4.48 cm | 4.19 to 4.80 cm | 0.048 | 0.021 to 0.070 | 3.64 cm | 0.372 | 0.01 | Similar to demographics-only; below imaging-only Ridge. |
| TAPSE | Demographics-only Ridge | 160 | `age_at_echo_approx`, `sex` | 3.79 mm | 3.80 mm | 3.36 to 4.24 mm | -0.024 | -0.095 to 0.020 | 3.17 mm | 0.284 | 0.01 | Performed near null; below imaging-only Ridge. |
| TAPSE | Study/acquisition metadata Ridge | 160 | `n_clips`, `n_dicoms` | 3.79 mm | 3.77 mm | 3.35 to 4.19 mm | 0.0004 | -0.046 to 0.012 | 3.17 mm | 0.284 | 100 | Metadata-only baseline performed near null. |
| TAPSE | Demographics + study/acquisition metadata Ridge | 160 | `age_at_echo_approx`, `sex`, `n_clips`, `n_dicoms` | 3.79 mm | 3.79 mm | 3.37 to 4.22 mm | -0.018 | -0.091 to 0.032 | 3.17 mm | 0.284 | 0.01 | Performed near null; below imaging-only Ridge. |

Footnote: Demographic features were approximate age at echo and sex. Approximate age at echo was derived from MIMIC-IV `anchor_age` and `anchor_year` adjusted by echo study year; all exported demographics rows used `study_datetime` as the study-year source in the aggregate run. Race/ethnicity was not included because a direct echo-study-to-admission linkage was not used for this leakage-safe baseline. Study/acquisition metadata were limited to the number of DICOMs and successfully embedded clips per study. No echo measurements, report text, diagnoses, post-echo variables, qualitative echo findings, or variables derived from echocardiogram interpretation were used.
