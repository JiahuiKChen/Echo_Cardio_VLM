# Phase 2.5 Figure Generation Runbook

## Purpose

Phase 2.5 generates manuscript-review figures for the stable-v2 imaging-only LVOT VTI and TAPSE baselines. Some figures require patient-level prediction CSVs and must be generated only on SCC under restricted storage.

## Governance Boundary

Allowed outputs:

- PNG/PDF figure files.
- `phase2_figure_inventory.csv`.
- `phase2_figure_metadata.json`.
- `phase2_figure_caption_drafts.md`.

Forbidden outputs:

- prediction CSV copies.
- row-level exports.
- raw embeddings or NPZ files.
- manifests, DICOM paths, logs, or patient identifiers.

Prediction-dependent figures are generated only when `--allow-restricted-predictions` is supplied and both `--output-root` and `--figure-dir` are under the restricted SCC Phase 2 output root.

## SCC Command

```bash
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
git fetch origin
git checkout codex/phase2-stable-imaging-baselines
git pull origin codex/phase2-stable-imaging-baselines

PY=.venv-echoprime/bin/python
OUT=/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2
FIGS=$OUT/review_packets/phase2_figures_restricted_review
mkdir -p "$FIGS"

$PY scripts/plot_phase2_imaging_results.py \
  --output-root "$OUT" \
  --figure-dir "$FIGS" \
  --review-packet-dir "$OUT/review_packets/phase2_verified_aggregate_only" \
  --allow-restricted-predictions \
  --format png,pdf \
  --dpi 300
```

## Expected Figures

LVOT VTI main figures:

- `fig1_lvot_observed_vs_predicted`
- `fig1_lvot_bland_altman`
- `fig1_lvot_mae_comparison`
- `fig1_lvot_low_vti_roc`

TAPSE supplementary figures:

- `figS1_tapse_observed_vs_predicted`
- `figS1_tapse_bland_altman`
- `figS1_tapse_mae_comparison`

LVOT VTI ECHOVIEW sensitivity figures:

- `figS2_lvot_echoview_sensitivity_mae`
- `figS2_lvot_echoview_sensitivity_r2`

## Review Notes

Figures should remain in restricted review storage until confirmed to contain no identifiers or patient-specific annotations. Only deidentified, manuscript-review figures should be exported outside SCC after review.
