# Supplementary Table S3. Exploratory Binary Threshold Summaries Derived From Continuous Predictions

Exploratory binary summaries derived from continuous stable-v2 all-clips predictions. These were not separately trained classifiers.

| Target | Threshold | Test N | Positives | Prevalence | AUROC | Average precision | Sensitivity | Specificity | PPV | NPV | F1 | Note |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| LVOT VTI | <18 cm | 555 | 106 | 0.191 | 0.846 | 0.569 | 0.358 | 0.960 | 0.679 | 0.864 | 0.469 | Exploratory thresholded summary of continuous LVOT VTI predictions |
| LVOT VTI | <20 cm | 555 | 184 | 0.332 | 0.812 | 0.669 | 0.478 | 0.911 | 0.727 | 0.779 | 0.577 | Exploratory thresholded summary of continuous LVOT VTI predictions |
| TAPSE | <17 mm | 160 | 36 | 0.225 | 0.789 | 0.638 | 0.472 | 0.944 | 0.708 | 0.860 | 0.567 | Exploratory thresholded summary of continuous TAPSE predictions |

Footnote: AUROC = area under the receiver operating characteristic curve; NPV = negative predictive value; PPV = positive predictive value. Thresholded summaries were derived from the continuous regression models and were not separately trained classifiers.
