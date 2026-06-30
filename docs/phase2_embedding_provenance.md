# Phase 2 EchoPrime Embedding Provenance

## Purpose

This document records how the Phase 2 TAPSE/LVOT VTI imaging-only analyses converted MIMIC-IV-ECHO DICOMs into frozen EchoPrime study embeddings. It is intended to keep manuscript language aligned with the actual model inputs and to prevent overclaiming direct measurement automation or EchoPrime fine-tuning.

## Raw Data Source

The full-scale pipeline selects studies from the BigQuery dataset `physionet-data.mimiciv_echo`, using `echo_record_list` for DICOM records and `echo_study_list` for structured-measurement linkage. DICOM files are downloaded from the MIMIC-IV-ECHO Google Cloud Storage bucket `mimic-iv-echo-1.0.physionet.org`.

The full-scale selection script filters to studies with available measurement linkage (`measurement_id IS NOT NULL`) and one study per subject under a deterministic subject-level selection policy. Therefore, the Phase 2 imaging cohort represents the processed MIMIC-IV-ECHO DICOM subset available through the project pipeline, not the entire structured-measurement denominator.

## DICOM Preprocessing

The full-scale SCC pipeline audits downloaded DICOM headers, writes `dicom_audit.csv`, and then defines `cine_candidates.csv` as readable DICOMs with `NumberOfFrames > 1`. The cine extraction step uses `scripts/extract_mimic_echo_cines.py` with no total clip cap and no per-study clip cap.

For each candidate multiframe DICOM, the extractor:

- reads pixel data with `pydicom`;
- excludes single-frame images;
- normalizes supported pixel array shapes to three-channel frames;
- applies a best-effort ultrasound-sector mask;
- center-crops/aspect-corrects and resizes frames to 224 x 224;
- samples 32 frames across the source frame sequence, padding short sequences with the final frame when needed;
- writes a compressed NPZ containing the sampled frames and source-frame metadata.

This means the Phase 2 embeddings are video/cine-derived embeddings from successfully processed multiframe DICOMs. Still-frame DICOMs are not included in the all-clips embedding store because the extraction path filters to multiframe DICOMs.

## EchoPrime Embedding Extraction

The full-scale pipeline calls `scripts/extract_echoprime_embeddings.py` with `--encoder-only`. The script loads the EchoPrime video encoder checkpoint `echo_prime_encoder.pt` into a `torchvision.models.video.mvit_v2_s()` backbone whose final projection is 512-dimensional. The model is set to evaluation mode and all parameters have `requires_grad = False`.

Each extracted clip is prepared as a tensor with shape `(3, 16, 224, 224)`: the stored 32 frames are normalized with fixed EchoPrime mean and standard deviation values, and every other frame is used to produce a 16-frame input sequence. In full-scale Phase 2 processing, the view-classifier branch is skipped, so the output is a 512-dimensional encoder embedding per successfully processed clip rather than a 523-dimensional encoder-plus-view vector.

Failed or unsupported clips are represented in the clip manifest with `write_ok = False` and are not used for downstream study-level aggregation.

## Clip-Level Versus Study-Level Embeddings

The immediate EchoPrime output is clip-level: one 512-dimensional embedding per successfully processed multiframe DICOM clip. The full-scale pipeline then merges per-batch clip-embedding files into `merged_clip_embeddings_512/clip_embeddings_512.npz` with a per-clip manifest.

The manuscript-facing Phase 2 all-clips analyses use study-level embeddings, not individual clip embeddings. `scripts/aggregate_study_embeddings.py` filters to successful clip rows and mean-pools all clip embeddings within each study. The output is:

- `study_embeddings_512/study_embeddings_512.npz`;
- `study_embeddings_512/study_embedding_manifest.csv`;
- one 512-dimensional mean-pooled vector per study.

The verified Phase 2 primary all-clips result used this study-level embedding store. The aggregate output reported an embedding matrix shape of 4,696 studies by 512 features.

## All-Clips Aggregation

In the primary Phase 2 analyses, "all-clips" means all successfully processed multiframe DICOM clips for a study were pooled into a single study vector. The aggregation method used by the full-scale pipeline was mean pooling. No target-specific clip selection, Doppler localization, M-mode localization, or measurement-view localization was applied to the primary all-clips embeddings.

## View-Filtered/ECHOVIEW Sensitivity Inputs

ECHOVIEW was used only for LVOT VTI view-filtered sensitivity analyses. The ECHOVIEW workflow joins ECHOVIEW probability columns to the clip-level embedding manifest by DICOM basename, then uses `scripts/aggregate_view_filtered_embeddings.py` to select clips according to view policies before study-level pooling.

The Phase 2 LVOT VTI sensitivity analyses used policies such as `a5c`, `other`, and `a5c_or_other` at prespecified probability thresholds. These view-filtered embeddings were also study-level pooled vectors, but over the selected ECHOVIEW subset rather than the full all-clips denominator. ECHOVIEW is a limited derived view-classification subset and is not the primary DICOM denominator.

## What "Frozen" Means

In the manuscript, "frozen EchoPrime embeddings" means:

- EchoPrime encoder weights were loaded from pretrained checkpoints and were not updated during Phase 2;
- clip-level EchoPrime embeddings were generated before downstream target modeling;
- study-level embeddings were fixed feature matrices during TAPSE/LVOT VTI model training;
- the only trained component in Phase 2 was a downstream Ridge regression model fit to the training split.

This should not be described as EchoPrime fine-tuning, end-to-end DICOM model training, or direct measurement extraction from raw DICOM pixels.

## What the Phase 2 Model Trained

For each target, `scripts/run_tapse_lvot_vti_imaging_baseline.py` joined structured report labels to study-level embeddings by `study_id`, applied deterministic subject-level splits by `subject_id`, and fit Ridge regression on the training split. Feature standardization was fit only on the training split, Ridge alpha was selected on the validation split, and the held-out test split was used for final evaluation.

Structured report measurements were used as labels only. They were not used as image-model inputs in the imaging-only Phase 2 baseline.

## Supported Claims

The code supports the following manuscript claims:

- The Phase 2 analysis used frozen EchoPrime encoder features as fixed imaging predictors.
- Primary LVOT VTI and secondary TAPSE analyses used mean-pooled study-level all-clips embeddings derived from successfully processed multiframe DICOM clips.
- The downstream supervised model was Ridge regression with train-fit standardization and validation-only alpha selection.
- The model predicts structured report measurements for LVOT VTI and TAPSE from fixed imaging embeddings.
- ECHOVIEW analyses are limited subset sensitivities based on post-hoc view-probability filtering before study-level pooling.

## Unsupported Claims

The code does not support the following claims:

- EchoPrime was fine-tuned for LVOT VTI or TAPSE.
- The model directly measured LVOT VTI from spectral Doppler traces.
- The model directly measured TAPSE from M-mode or tricuspid-annular motion clips.
- The primary all-clips embedding was restricted to clinically measurement-specific views.
- ECHOVIEW-filtered analyses prove superior view selection.
- The analysis used all MIMIC-IV-ECHO structured-measurement studies or all DICOM objects, including still frames.
- The model is measurement-grade automation or ready for clinical deployment.

## Doppler and M-Mode Verification Status

The Phase 2 all-clips code does not explicitly exclude Doppler or M-mode DICOMs if they are readable multiframe DICOMs that pass extraction and embedding. A reviewer follow-up aggregate retention audit summarized the processed full-scale pipeline as follows:

- downloaded/readable DICOM audit rows: 311,043;
- single-frame or still DICOMs: 140,443;
- multiframe candidates: 170,600;
- successfully extracted clips: 170,600;
- successfully embedded clips: 191,993;
- study embeddings: 4,696.

However, the available metadata fields were insufficient to classify retained clips reliably as Doppler, spectral Doppler, color Doppler, M-mode, or 2D/cine. Keyword matching over available fields yielded zero category matches, with records assigned to `keyword_unknown_or_unmatched`. This should be interpreted as an insufficiency of the available metadata fields, not as evidence that Doppler or M-mode content was absent.

For manuscript language, it is safest to say that the primary model used all successfully processed multiframe DICOM clips and did not apply Doppler- or M-mode-specific localization. Do not claim direct processing of the specific LVOT VTI spectral trace or TAPSE M-mode measurement clip unless a future audit confirms this from DICOM metadata, view labels, or manual review.

## Open TODOs

- Verify the exact MIMIC-IV-ECHO release/version string used for the full-scale SCC pipeline.
- Determine whether additional DICOM metadata fields, vendor-specific tags, ECHOVIEW labels, or manual review can identify Doppler/M-mode retention without exporting row-level restricted data.
- If measurement-view claims become central, run a separate measurement-view localization audit rather than relying on all-clips study embeddings.
