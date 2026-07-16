# Table 1. Main Continuous Performance Metrics

Main imaging-only baseline performance using frozen EchoPrime study embeddings and deterministic subject-level train/validation/test splits. Ridge models used train-fit feature standardization, the `svd` solver, and validation-only alpha selection. MAE and RMSE units are cm for LVOT VTI and mm for TAPSE.

| Target | Role | Test N | Null MAE | Ridge MAE | Ridge MAE 95% CI | Ridge RMSE | Ridge R2 | Ridge R2 95% CI | Selected alpha | Bland-Altman bias | Bland-Altman limits of agreement | Figure callout |
|---|---|---:|---:|---:|---|---:|---:|---|---:|---:|---|---|
| LVOT VTI | Primary | 555 | 4.54 cm | 3.64 cm | 3.41 to 3.88 cm | 4.66 cm | 0.372 | 0.30 to 0.43 | 0.3 | +0.28 cm | -8.85 to +9.41 cm | Figure 1 |
| TAPSE | Secondary | 160 | 3.79 mm | 3.17 mm | 2.80 to 3.56 mm | 3.94 mm | 0.284 | 0.13 to 0.40 | 1000 | +0.22 mm | -7.52 to +7.96 mm | Supplementary Figure S1 |

Footnote: CI = confidence interval; LVOT VTI = left ventricular outflow tract velocity-time integral; MAE = mean absolute error; RMSE = root mean squared error; TAPSE = tricuspid annular plane systolic excursion.
