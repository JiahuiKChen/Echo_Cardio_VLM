# Supplementary Table S2. LVOT VTI ECHOVIEW View-Filtered Sensitivity Analyses

LVOT VTI sensitivity analyses using ECHOVIEW view-filtered embeddings. These analyses are limited subset analyses and were not used as the primary modeling denominator.

| View policy | Threshold | Test N | Ridge MAE | Ridge R2 | Bootstrap CI note | Status | Interpretation note |
|---|---:|---:|---:|---:|---|---|---|
| A5C-or-other | 0.70 | 65 | 4.07 cm | 0.151 | MAE 3.32 to 4.83 cm; R2 -0.06 to 0.32 | Completed | Smaller ECHOVIEW subset |
| Other-only | 0.70 | 65 | 4.14 cm | 0.123 | MAE 3.36 to 4.93 cm; R2 -0.12 to 0.30 | Completed | Smaller ECHOVIEW subset |
| A5C-only | 0.70 | 22 | Not estimated | Not estimated | Not available | Skipped/underpowered | Insufficient training data |
| A5C-or-other | 0.80 | 65 | 4.10 cm | 0.140 | MAE 3.34 to 4.87 cm; R2 -0.10 to 0.32 | Completed | Higher threshold did not improve performance |
| A5C-or-other | 0.90 | 65 | 4.14 cm | 0.118 | MAE 3.36 to 5.01 cm; R2 -0.13 to 0.32 | Completed | Higher threshold did not improve performance |
| A5C-or-other | 0.95 | 65 | 4.14 cm | 0.119 | MAE 3.38 to 4.96 cm; R2 -0.12 to 0.31 | Completed | Higher threshold did not improve performance |

Footnote: A5C-only at threshold 0.70 was skipped because of insufficient training data and should not be interpreted as a negative result.
