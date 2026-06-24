# Echo Cardio VLM Claude Code Rules

Follow `AGENTS.md` as the canonical project rule file. This short mirror exists for Claude Code sessions.

## Purpose

This repo is a reproducible MIMIC-IV-ECHO pipeline for SCC-based DICOM processing, EchoPrime embeddings, structured measurement audits, and manuscript-safe aggregate reporting. Current Phase 1 work is audit infrastructure for TAPSE and LVOT VTI feasibility, not modeling.

## Hard Rules

- Do not commit DUA-governed data, PHI, raw DICOMs, patient-level manifests, SCC-only artifacts, note text, or patient-level outputs.
- Keep raw DICOMs and original manifests immutable.
- Patient-level outputs remain in approved restricted paths. Only aggregate manuscript-safe outputs may enter the repo.
- Do not hard-code SCC paths; accept paths through CLI arguments and provide example commands.
- Use deterministic subject-level splits only.
- Do not train or evaluate TAPSE/LVOT VTI models until Phase 1 audits pass.

## ECHOVIEW And Targets

- ECHOVIEW thresholds are sensitivity parameters, not truth.
- ECHOVIEW is a derived subset, not the whole MIMIC-IV-ECHO DICOM corpus.
- TAPSE: primary view policy is A4C-family; A4C-family plus RV inflow is sensitivity.
- LVOT VTI: primary feasibility must be Doppler-sensitive using A5C-or-other; strict A5C-only is sensitivity.
- TAPSE and LVOT VTI require target-specific leakage exclusions before any tabular or fusion comparator.

## Required Audit Artifacts

Before modeling, produce aggregate reports for denominator funnels, ECHOVIEW join coverage, duplicate/unmatched checks, target distributions, threshold counts, split integrity, leakage exclusions, and checksums where applicable.
