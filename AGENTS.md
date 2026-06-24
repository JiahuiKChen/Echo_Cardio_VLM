# Echo Cardio VLM Agent Rules

This repository contains a reproducible, SCC-first MIMIC-IV-ECHO research pipeline for cohort construction, DICOM processing, EchoPrime embedding extraction, structured measurement audits, and manuscript-safe aggregate reporting.

## Layout

- `scripts/`: cohort, preprocessing, embedding, audit, and baseline utilities.
- `docs/`: PRD, SCC operations notes, methods plans, and manuscript-safe snapshots.
- `docker/`: GPU container and smoke-test support.
- `outputs/`: only small smoke-test or manuscript-safe artifacts may be tracked.

## Data Governance

- Never commit DUA-governed data, PHI, raw DICOMs, patient-level manifests, SCC-only artifacts, note text, or patient-level model outputs.
- Raw DICOMs and original manifests are immutable.
- Patient-level outputs must stay in approved restricted storage. Only aggregate manuscript-safe snapshots may enter the repo.
- Do not hard-code restricted SCC paths in code. Accept paths through CLI arguments and document example SCC commands.
- Prefer post-hoc scripts against frozen artifacts over modifying large SCC runners.

## Scientific Rules

- All splits must be deterministic and subject-level. No subject may cross train, validation, and test.
- ECHOVIEW probabilities are probabilistic view annotations. Thresholds such as 0.70, 0.80, 0.90, and 0.95 are sensitivity parameters, not ground truth.
- ECHOVIEW is a derived subset of MIMIC-IV-ECHO; low ECHOVIEW overlap must not be interpreted as absence of views in the full DICOM corpus.
- TAPSE primary feasibility uses A4C-family probabilities; A4C-family plus RV inflow is a sensitivity policy.
- LVOT VTI primary feasibility must be Doppler-sensitive. Evaluate A5C-only, other-only, A5C-or-other, and all-clips comparators; strict A5C-only is not primary.
- Do not model TAPSE or LVOT VTI until denominator, ECHOVIEW join, target feasibility, leakage, and split-integrity audits pass.
- After those audits pass, Phase 2 may run imaging-only baselines from EchoPrime embeddings. Prediction files and view-filtered embedding manifests are patient-level restricted outputs and must stay outside the repo.
- LVOT VTI is the Phase 2 priority target. TAPSE is a cautious secondary target; ECHOVIEW-filtered TAPSE analyses are sensitivity analyses, not the primary denominator.

## Leakage Rules

- TAPSE models must exclude TAPSE direct fields/synonyms, tricuspid annular plane systolic excursion, RV systolic/function summary fields, S prime/S' tissue Doppler, and RV fractional area change unless explicitly framed as comparator/leakage experiments.
- LVOT VTI models must exclude LVOT VTI direct fields/synonyms, AV/aortic valve VTI, stroke volume, cardiac output, cardiac index, LVOT stroke distance, and direct derivatives unless explicitly framed as comparator/leakage experiments.

## Required Audit Outputs Before Modeling

Generate aggregate, manuscript-safe outputs for:

- denominator/funnel accounting,
- ECHOVIEW join coverage,
- duplicate checks and many-to-many checks,
- unmatched row summaries,
- target distributions and implausible values,
- threshold counts by view policy,
- split counts and split-integrity checks,
- target-specific leakage exclusions,
- checksums where applicable.

Fail closed when required restricted data are unavailable: report missing inputs and print exact SCC commands rather than inventing counts. Any baseline script that writes patient-level predictions or embedding manifests should refuse repo-local output directories unless explicitly run in synthetic-test mode.
