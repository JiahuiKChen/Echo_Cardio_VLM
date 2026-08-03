# Manuscript scope

## Primary concept

**Leakage-aware multimodal quantitative echocardiographic report completion using frozen whole-study EchoPrime representations and available structured measurements.**

Primary readership is cardiovascular imaging, digital health, and medical AI. Anesthesiology, perioperative medicine, and critical care are secondary translational audiences.

## Primary question

Among one-study-per-subject MIMIC-IV-ECHO studies with observed quantitative report labels, does adding a frozen whole-study echocardiographic representation to leakage-minimized, realistically masked structured measurements improve held-out report-label prediction over either modality alone on identical subjects?

## Analysis hierarchy

1. Historical LVEF result, preserved as accepted context.
2. Confirmatory LVEF revalidation with target-family leakage controls and paired uncertainty.
3. Primary strict leakage-minimized measurement construct using the final clinically adjudicated panel; the current 21-target list is provisional and has no locked version name.
4. Secondary target-family-masked report-completion construct using the same final scored targets and a broader prespecified predictor mask.
5. Secondary pragmatic same-report completion construct using the same final scored echo targets; context/metadata targets remain outside the primary echo macro.
6. Simulated single-target and target-family masking.
7. Optional validation-calibrated selective prediction after primary revalidation.

The former provisional labels `strict21-v1` and `pragmatic26-v1` are superseded hypotheses retained only in the historical OpenEvidence prompts. They are not current panel authorities.

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
