# Manuscript scope

## Primary concept

**Leakage-aware multimodal quantitative echocardiographic report completion using frozen whole-study EchoPrime representations and available structured measurements.**

Primary readership is cardiovascular imaging, digital health, and medical AI. Anesthesiology, perioperative medicine, and critical care are secondary translational audiences.

## Primary question

Among one-study-per-subject MIMIC-IV-ECHO studies with observed quantitative report labels, does adding a frozen whole-study echocardiographic representation to leakage-minimized, realistically masked structured measurements improve held-out report-label prediction over either modality alone on identical subjects?

## Analysis hierarchy

1. Historical LVEF result, preserved as accepted context.
2. Confirmatory LVEF revalidation with target-family leakage controls and paired uncertainty.
3. Primary `strict21-v1` leakage-minimized echo panel.
4. Secondary `pragmatic26-v1` same-report completion panel.
5. Simulated single-target and target-family masking.
6. Optional validation-calibrated selective prediction after primary revalidation.

## Required claim boundaries

The study may evaluate frozen-representation utility, multimodal incremental value, observed quantitative report-label prediction, and simulated report completion.

It must not claim direct pixel-level measurement automation, accuracy for genuinely missing labels without reference truth, autonomous report generation, causal clinical utility, improved outcomes, perioperative risk prediction, clinical deployment, or generative AI performance.

LVEF, LVOT VTI, TAPSE, MAPSE, ventricular-function and hemodynamic measurements may motivate perioperative/critical-care relevance. No perioperative outcome claim is permitted without a separately designed outcome-linkage analysis.

## Deferred work

- all-study/repeated-study expansion;
- learned pooling or a new transformer;
- raw-DICOM end-to-end modeling;
- perioperative outcome linkage;
- generative report or video modeling.

All-study expansion requires a prespecified longitudinal estimand, patient-fixed splits, subject-clustered inference, and separate study-weighted and subject-weighted reporting.
