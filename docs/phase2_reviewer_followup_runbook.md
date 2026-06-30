# Phase 2 Reviewer Follow-Up Runbook

This runbook covers reviewer-suggested aggregate-only follow-up analyses for PR #1. These commands should be run on SCC against restricted project storage. Do not copy row-level predictions, figure-ready CSVs, raw embeddings, DICOM manifests, or logs into the repository.

## 1. Leakage-Safe Non-Image Baselines

The non-image baseline runner uses the same structured targets, embedding-available study denominator, and subject-level train/validation/test split as the Phase 2 imaging baseline. It writes aggregate metrics only by default.

```bash
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM
git fetch origin
git checkout codex/phase2-stable-imaging-baselines
git pull origin codex/phase2-stable-imaging-baselines

PY=.venv-echoprime/bin/python
OUT=/restricted/project/mimicecho/outputs/tapse_lvot_vti_phase2_stable_v2

$PY scripts/run_phase2_nonimage_baselines.py \
  --output-root "$OUT" \
  --structured-measurements-csv outputs/cloud_cohorts/fullscale_all/manifests/structured_measurements.csv \
  --study-embedding-npz outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embeddings_512.npz \
  --study-embedding-manifest outputs/cloud_cohorts/fullscale_all/study_embeddings_512/study_embedding_manifest.csv \
  --subject-split-map-csv outputs/cloud_cohorts/fullscale_all/manifests/subject_split_map_v1.csv \
  --selected-studies-csv outputs/cloud_cohorts/fullscale_all/manifests/all_eligible_studies.csv \
  --targets lvot_vti,tapse \
  --aggregate-only
```

If an approved demographics file is available, add:

```bash
  --demographics-csv /restricted/project/mimicecho/metadata/<approved_demographics_file>.csv
```

The demographics CSV must contain `subject_id` and leakage-safe demographic columns such as age, sex/gender, and optionally race/ethnicity. It must not contain echo measurements, report text, diagnoses, indications, LVEF, qualitative echo findings, or anything derived from the echocardiogram interpretation.

Expected aggregate outputs:

- `$OUT/nonimage_baselines/phase2_nonimage_baseline_metrics.csv`
- `$OUT/nonimage_baselines/phase2_nonimage_baseline_binary_metrics.csv`
- `$OUT/nonimage_baselines/phase2_nonimage_baseline_bootstrap_ci.csv`
- `$OUT/nonimage_baselines/phase2_nonimage_baseline_binary_bootstrap_ci.csv`
- `$OUT/nonimage_baselines/phase2_nonimage_baseline_alpha_selection.csv`
- `$OUT/nonimage_baselines/phase2_nonimage_baseline_summary.json`
- `$OUT/nonimage_baselines/phase2_nonimage_baseline_warnings.json`

## 2. TAPSE <17 mm Binary Summary

The stable-v2 imaging baseline already defines TAPSE `<17 mm` as an exploratory threshold in `scripts/run_tapse_lvot_vti_imaging_baseline.py`. The aggregate verifier now collects TAPSE binary rows when `imaging_baseline_binary_metrics.csv` is present for `tapse/all_clips`.

To refresh the aggregate-only verification packet after the script update:

```bash
REVIEW=$OUT/review_packets/phase2_verified_aggregate_only
mkdir -p "$REVIEW"

$PY scripts/summarize_phase2_imaging_results.py \
  --output-root "$OUT" \
  --output-dir "$REVIEW" \
  --draft-md docs/phase2_results_manuscript_draft.md \
  --write-markdown-summary
```

Check:

```bash
cat "$REVIEW/phase2_binary_low_vti_verified.csv"
cat "$REVIEW/phase2_results_verified_summary.md"
```

Despite the historical filename, `phase2_binary_low_vti_verified.csv` can now include both LVOT VTI low-flow thresholds and TAPSE `<17 mm` if aggregate TAPSE binary metrics are available.

## 3. Doppler/M-Mode Retention Audit

The retention audit reads existing DICOM audit and clip/embedding manifests, writes aggregate counts only, and does not open pixel data.

```bash
DOPPLER_AUDIT=$OUT/review_packets/phase2_doppler_mmode_retention_audit
mkdir -p "$DOPPLER_AUDIT"

$PY scripts/audit_phase2_doppler_mmode_retention.py \
  --output-root "$OUT" \
  --fullscale-root outputs/cloud_cohorts/fullscale_all \
  --output-dir "$DOPPLER_AUDIT" \
  --phase2-output-root "$OUT" \
  --aggregate-only
```

Expected aggregate outputs:

- `$DOPPLER_AUDIT/phase2_doppler_mmode_stage_counts.csv`
- `$DOPPLER_AUDIT/phase2_doppler_mmode_keyword_counts.csv`
- `$DOPPLER_AUDIT/phase2_doppler_mmode_target_summary_counts.csv`
- `$DOPPLER_AUDIT/phase2_doppler_mmode_retention_summary.json`
- `$DOPPLER_AUDIT/phase2_doppler_mmode_retention_warnings.json`
- `$DOPPLER_AUDIT/phase2_doppler_mmode_retention_summary.md`

Interpretation caveat: DICOM metadata are vendor-specific and may be incomplete. Keyword categories are screening counts, not adjudicated view labels. If metadata are insufficient, keep the manuscript limitation that the current all-clips model was not localized to LVOT VTI Doppler traces or TAPSE M-mode/tricuspid-annular motion clips.

## 4. Decision Logic

- If demographics-only performance is much worse than imaging-only, the manuscript can say the embedding baseline outperformed a leakage-safe non-image baseline.
- If demographics-only performance is similar to imaging-only, weaken imaging-specific language and frame the result as not clearly above simple metadata.
- If TAPSE `<17 mm` has adequate positives and reasonable AUROC, report it as supplementary exploratory binary analysis.
- If TAPSE `<17 mm` is underpowered, keep it in supplement or internal notes only.
- If Doppler/M-mode retention cannot be verified from metadata, keep current limitation language.
- If Doppler/M-mode clips are retained, clarify that all-clips embeddings may include measurement-relevant clips but were not localized to measurement traces.
