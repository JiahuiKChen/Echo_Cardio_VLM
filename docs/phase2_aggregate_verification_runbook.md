# Phase 2.4 Aggregate Verification Runbook

## Purpose

Phase 2.4 verifies the stable-v2 imaging-only TAPSE and LVOT VTI results from SCC aggregate outputs before manuscript tables are frozen. The verifier reads only aggregate metric files and produces a compact review packet with manuscript-ready tables.

The verifier does not read, copy, or summarize patient-level prediction rows.

## Allowed Inputs

The verifier reads only these files from each completed run directory:

- `imaging_baseline_summary.json`
- `imaging_baseline_metrics.csv`
- `imaging_baseline_ridge_alpha_selection.csv`
- `imaging_baseline_bootstrap_ci.csv`
- `imaging_baseline_binary_metrics.csv`

## Forbidden Inputs

Do not copy or commit:

- `imaging_baseline_predictions.csv`
- any prediction-level CSV
- patient-level manifests
- raw embeddings or NPZ files
- SCC logs
- DICOM paths or identifiers
- PHI or DUA-governed data

The verifier inventories restricted-looking files by filename and reports that they were excluded, but it does not parse them.

## SCC Command

Run from the SCC checkout after pulling `codex/phase2-stable-imaging-baselines`:

```bash
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
git fetch origin
git checkout codex/phase2-stable-imaging-baselines
git pull origin codex/phase2-stable-imaging-baselines

PY=.venv-echoprime/bin/python
OUT=/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2
REVIEW=$OUT/review_packets/phase2_verified_aggregate_only
mkdir -p "$REVIEW"

$PY scripts/summarize_phase2_imaging_results.py \
  --output-root "$OUT" \
  --output-dir "$REVIEW" \
  --draft-md docs/phase2_results_manuscript_draft.md \
  --write-markdown-summary
```

## Expected Outputs

The verifier writes aggregate-only files under `$REVIEW`:

- `phase2_primary_results_verified.csv`
- `phase2_echoview_sensitivity_verified.csv`
- `phase2_binary_low_vti_verified.csv`
- `phase2_run_inventory.csv`
- `phase2_metric_verification_warnings.json`
- `phase2_results_verified_summary.md`

These outputs are candidates for manuscript review after confirming they contain no identifiers. They should not be committed to the repo unless explicitly approved as manuscript-safe snapshots.

## Manuscript Update Workflow

Use `phase2_results_verified_summary.md` to update `docs/phase2_results_manuscript_draft.md`.

The main manuscript table should include only:

- LVOT VTI all-clips stable-v2.
- TAPSE all-clips stable-v2.

Hard-extreme exclusions should be reported as a compact robustness note in the main table and as Supplementary Table S1. ECHOVIEW/view-filtered LVOT VTI analyses should be Supplementary Table S2. Binary low-VTI metrics should be Supplementary Table S3 or an exploratory appendix section only after exact aggregate extraction.
