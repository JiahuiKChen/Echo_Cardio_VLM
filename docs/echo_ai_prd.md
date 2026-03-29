# PRD: Multimodal Echocardiography Analysis on MIMIC-IV-ECHO

## Project title

Multimodal echocardiography analysis: systematic evaluation of vision, structured measurements, and clinical notes on public data

## Executive summary

This project builds the first multimodal echocardiography prediction pipeline on public data, combining video embeddings, structured quantitative measurements, and clinical notes from MIMIC-IV-ECHO. The core contribution is a modality-isolation evaluation ladder that systematically quantifies what each data source contributes to cardiac function prediction — a methodological gap in the current literature. Existing published work on MIMIC-IV-ECHO uses video alone (Echo-Vision-FM, MVE-Echo) or video with proprietary text (EchoPrime). No published work has used structured measurements or MIMIC clinical notes as input features alongside vision for prediction. The project preserves a clean path toward measurement-conditioned echo generation as a stretch goal.

## Problem statement

Public echocardiography foundation and generative work is limited by three practical constraints:

- public video datasets are much smaller and noisier than large proprietary echo corpora
- multi-view echo studies contain heterogeneous clips with weak clip-level labels
- raw DICOM storage, preprocessing, and reproducibility are major engineering bottlenecks

For MIMIC-IV-ECHO specifically, the public imaging release is a TTE-only subset of the broader structured measurement resource. That makes direct large-scale conditional video generation from scratch high risk. A lower-risk sequence is needed.

## Why this matters clinically and scientifically

Clinically, echocardiography is central to assessment of ventricular function, chamber size, valvular disease, hemodynamics, and longitudinal cardiac monitoring. Scientifically, robust echo video representations and reconstruction models could support:

- more data-efficient downstream modeling
- better cross-task transfer on limited public datasets
- future synthetic data generation for rare findings, imbalance mitigation, and privacy-aware method development
- a pathway to multimodal conditioning with reports and structured measurements

## Competitive landscape

Three published works have used MIMIC-IV-ECHO. Understanding exactly what each did — and did not do — defines our contribution.

### Echo-Vision-FM (Zhang et al., Nature Communications 2026)

- VideoMAE pre-training on all 525K MIMIC-IV-ECHO videos (self-supervised, no labels)
- ViT-Base encoder, 85% mask ratio, 30 epochs on A100 GPUs
- Fine-tuned and evaluated on EchoNet-Dynamic, CAMUS, and TMED (not on MIMIC itself)
- LVEF MAE 3.87%, AUC 0.931 on EchoNet-Dynamic
- Claims first public-data echo video foundation model
- **Did not use** MIMIC structured measurements or clinical notes at all
- **Did not evaluate** on MIMIC-IV-ECHO downstream tasks

### MVE-Echo (Tohyama et al., medRxiv 2025)

- Multi-view encoder using masked transformer on top of EchoPrime 512-d embeddings
- Processed all 7,169 studies (median 41 videos/study) from MIMIC-IV-ECHO
- Attention-based aggregation of per-video embeddings into study-level 512-d vectors
- Evaluated on 21 binary classification tasks defined from structured measurements and ICD codes
- Structured measurements used as **prediction labels only**, never as input features
- Adversarial debiasing for sex and race (limited effectiveness)
- **Did not use** structured measurements as input features
- **Did not use** clinical notes

### EchoPrime (Vukadinovic et al., 2024)

- Multi-view vision-language model trained on 12M proprietary Stanford video-report pairs
- Contrastive learning between video embeddings and clinical report text
- View classifier (11 classes) exhibits domain shift on MIMIC-IV-ECHO data (69% SSN misclassification confirmed by our diagnostic)
- 512-d encoder features generalize well (our E2 baseline: test AUC 0.924)
- **Did not use** MIMIC data for training; used proprietary text only

### What remains unclaimed

| Gap | Description |
|-----|-------------|
| Measurements as input features | No paper uses structured measurements alongside vision for prediction |
| Clinical notes integration | No paper uses MIMIC clinical notes for echo analysis |
| Modality-isolation ablation | No systematic E1–E6 ladder quantifying each source's contribution |
| Multi-video study-level embeddings + measurements | MVE-Echo aggregates videos but never adds tabular features |
| Measurement-conditioned generation | No conditional echo generation on public data |

## Why MIMIC-IV-ECHO is an appropriate starting dataset

MIMIC-IV-ECHO provides:

- public TTE DICOM studies
- study-level linkage tables
- structured quantitative measurements
- patient linkage to broader MIMIC-IV data

Official documentation indicates:

- `structured_measurement` covers 206,488 echo studies from 91,372 patients
- the public DICOM subset covers about 525,000 DICOMs across 7,243 TTE studies from 4,579 patients
- `echo-record-list.csv` links DICOM files to `study_id` and `subject_id`
- `echo-study-list.csv` links DICOM studies to nearby structured measurements and, where available, note metadata

This is enough to build a real public pipeline, but not enough to justify a direct large text-to-video program from scratch.

## Primary users and stakeholders

- research engineer building the pipeline
- ML researcher running experiments
- future clinical collaborators evaluating plausibility and utility
- manuscript reviewers who need reproducible data handling and defensible evaluation

## Goals

- Build a deterministic metadata-to-manifest pipeline for MIMIC-IV-ECHO.
- Extract EchoPrime embeddings from ALL multiframe DICOMs per study (~41 videos/study), not a small subset.
- Establish vision-only, measurement-only, and multimodal baselines across the evaluation ladder.
- Demonstrate incremental value of each modality (vision, measurements, notes) for cardiac prediction.
- Scale to all ~7,000 MIMIC-IV-ECHO studies for publication-quality results.
- Preserve a path toward measurement-conditioned echo generation.

## Non-goals

- Replicating VideoMAE pre-training (Echo-Vision-FM already published this on MIMIC).
- Training a large direct text-to-video model.
- Claiming clinical deployment readiness.
- View-filtering by EchoPrime classifier (confirmed unreliable on MIMIC due to domain shift).

## Hypotheses

- H1: Adding structured measurements (excl. LVEF) as input features alongside vision embeddings will improve LVEF prediction beyond vision-only baselines.
- H2: Clinical notes contain complementary signal not captured by vision or measurements alone.
- H3: Multi-video study-level embeddings (using all ~41 clips/study via attention aggregation) will outperform single-clip or 2-clip embeddings.
- H4: A modality-isolation evaluation ladder will produce the clean ablation table needed for a strong publication narrative.
- H5: Measurement-conditioned echo generation is feasible as a stretch goal once the multimodal pipeline is validated.

## Data sources

- MIMIC-IV-ECHO metadata tables and DICOM study files
- MIMIC-IV Note linkage, if accessible and permitted
- EchoPrime pretrained release for smoke test and encoder reuse
- attached reference papers for evaluation and manuscript framing

## System inputs and outputs

### Inputs

- `echo-record-list.csv`
- `echo-study-list.csv`
- `structured_measurement.csv` or `structured-measurement.csv.gz` after filename verification
- selected DICOM study folders under `files/...`
- optional note text from linked MIMIC-IV notes

### Outputs

- metadata summary reports
- study-level manifest
- clip-level manifest
- normalized clip cache
- QC summaries
- pretrained baseline outputs and embeddings
- reconstruction model checkpoints and sample reconstructions
- evaluation tables and figures

## Core workflows

1. Metadata inspection and schema validation.
2. Stage-wise study selection under storage limits.
3. Raw DICOM extraction and clip normalization.
4. View filtering and quality control.
5. Baseline encoder smoke testing and optional embedding extraction.
6. First reconstruction model training.
7. Offline evaluation and go/no-go decision for scale-up or conditional generation.

## Technical architecture

### Data layer

- raw study folders remain immutable
- manifests capture all derived relationships
- derived clips are stored separately from raw DICOMs

### Preprocessing layer

- DICOM decode
- clip normalization
- optional sector masking
- frame sampling
- optional coarse view prediction
- deterministic manifest emission

### Baseline layer

- EchoPrime encoder-only smoke test
- optional EchoPrime feature extraction or view classification baseline

### Modeling layer

- Study-level aggregation: attention-weighted pooling over all per-clip EchoPrime 512-d embeddings
- Multimodal fusion MLP: concatenated vision embeddings + structured measurement features
- Optional stretch: measurement-conditioned echo generation

### Evaluation layer

- fidelity and reconstruction quality
- anatomical and view plausibility
- measurement consistency
- representation utility for downstream tasks
- qualitative panels for manuscript figures

## Success metrics

### Milestone 1 (COMPLETE — 2026-03-28)

- metadata and manifest pipeline runs end to end on a pilot subset
- DICOM ingestion succeeds on a nontrivial sample
- clip normalization is deterministic

### Milestone 2 (COMPLETE — 2026-03-28)

- Stage D 500-study pipeline completed on SCC
- 37,961 DICOMs across 500 studies, zero read failures
- 21,393 cine clips, 16,568 still images
- 489 studies with usable LVEF measurements
- LVEF range 10–85 (median 55), good clinical spread
- Raw pixel baseline establishes floor: test AUC ≈ 0.37 (at chance)
- study and clip counts after QC are known

### Milestone 3 (COMPLETE — 2026-03-29)

- EchoPrime embedding baseline: **test AUC = 0.924** (study-level), vastly above the E1 floor of 0.37
- 998/998 clips embedded successfully, 499 studies, 489 with LVEF labels
- Study-level test metrics: AUC 0.924, AP 0.797, R² 0.446, MAE 8.4 EF points
- Val/test gap minimal (AUC 0.915 vs 0.924), no overfitting concern
- Confirms pretrained EchoPrime features capture cardiac function from video alone
- Ridge regression on frozen 523-d embeddings — no fine-tuning needed for strong signal

### Milestone 4 (COMPLETE — 2026-03-29)

- Multi-video extraction: 21,393 clips from 499 studies (median 42.9 clips/study)
- 512-d encoder-only embeddings (view classifier dropped), mean-pooled to study-level
- **Test AUC = 0.985** (up from 0.924 with 2 clips), AP 0.942, R² 0.588, MAE 6.7 EF points
- H3 validated: using all ~43 videos/study vs 2 improves AUC by +0.061, MAE by -1.7 pts
- Val/test gap minimal (0.967 vs 0.985), no overfitting

### Milestone 5

- Structured measurement baseline (E3) and first multimodal fusion (E5) evaluated on 500 studies
- Tabular ceiling vs vision vs fusion comparison table complete

### Milestone 6

- Full 7,000+ study extraction via batch-and-purge
- Final evaluation table across E1–E6 with publication-quality statistics and confidence intervals

### Milestone 7

- Clinical notes integration (E4, E6) if note access confirmed
- Manuscript draft with complete ablation table

## Evaluation strategy: modality isolation

A core principle of this project is strict modality isolation in the evaluation stack.
Each source of information (vision, structured measurements, clinical notes) must be
evaluated independently before any multimodal combination is tested. This prevents
data leakage, enables clean ablation tables, and produces the layered contribution
narrative that journal reviewers expect.

### Evaluation ladder (each row is a distinct experiment)

| Row | Input modality | Model | What it proves | Status |
|-----|---------------|-------|---------------|--------|
| E1 | Raw keyframe pixels | PCA + Ridge | Floor baseline | **Done**: test AUC ≈ 0.37 |
| E2a | EchoPrime 512-d clip embeddings (2 clips/study) | Ridge | Do pretrained features capture function? | **Done**: test AUC = 0.924 |
| E2b | EchoPrime 512-d embeddings (ALL clips/study, mean-pooled) | Ridge | Does multi-video aggregation improve over 2-clip? | **Done**: test AUC = 0.985 |
| E3 | Structured measurements only (excl. LVEF) | Ridge / LogReg | Tabular ceiling — non-imaging upper bound | Pending |
| E4 | Clinical notes embeddings | Ridge / LogReg | Text-only prediction ceiling | Pending (requires note access) |
| E5 | Vision + measurements (excl. LVEF) | Fusion MLP | Does multimodal fusion add value over either alone? | Pending |
| E6 | Vision + measurements + notes | Fusion MLP | Full multimodal contribution | Pending |

**Critical design rules:**
- Structured measurements must explicitly exclude LVEF and LVEF-derived fields when LVEF is the prediction target, to prevent leakage.
- Each row uses the same train/val/test split (subject-level, frozen).
- Vision embeddings use all available multiframe DICOMs per study (~41 median), not a 2-clip subset.
- The 11-d view one-hot from EchoPrime's view classifier is dropped (confirmed unreliable on MIMIC due to domain shift). Only the 512-d encoder features are used.

### DICOM utilization strategy

Prior work (MVE-Echo) processed a **median of 41 videos per study**. Our initial pipeline extracted only 2 clips per study (~5% utilization). For publication-quality results, we must extract and embed **all multiframe DICOMs per study**, then aggregate to study-level embeddings via attention or mean pooling.

Storage strategy for full extraction: batch-and-purge. Download ~500 studies at a time, extract all clips, compute EchoPrime embeddings, delete raw DICOMs, repeat. Final stored artifacts (embeddings + manifests) are small (~1 GB for all 7,000 studies).

### Role of structured measurements

Structured measurements serve dual roles depending on the experiment:

1. **As prediction targets** (following MVE-Echo's 21-task framework): LVEF < 50%, impaired RV function, valve disease severity, E/e' > 15, etc.
2. **As input features** (our novel contribution): Non-LVEF measurements alongside vision embeddings to test multimodal fusion.

Additionally:
- **Evaluation signal**: Do model predictions agree with structured measurements?
- **Conditioning input** (stretch goal): Generate echoes conditioned on target measurements.

### Publication-quality evaluation (adapted from Nature CXR paper)

#### For reconstruction (Phase 5–6)

- pixel-space metrics: MSE, MAE, PSNR, SSIM
- latent or feature-space reconstruction similarity using a frozen encoder
- view consistency score from a frozen view classifier
- structured-measurement prediction agreement (measurements as evaluation, not input)
- clinician-style qualitative review of view identity and major anatomy

#### For representation utility (Phase 4)

- linear probe or lightweight regressor on LVEF from embeddings alone
- retrieval of nearest clips or studies by learned embedding similarity
- optional downstream task transfer on public datasets if format alignment is manageable

#### For conditional generation (Phase 6+)

- fidelity and diversity metrics adapted to echo
- expert reader study
- view-conditional correctness
- measurement-conditioned consistency (measurements as conditioning input)
- downstream augmentation utility

## Ablation ideas

- 2 clips/study versus all clips/study (H3 validation)
- Mean pooling versus attention aggregation for multi-video embeddings
- Which structured measurement features contribute most (feature importance)
- LVEF leakage tests: performance with vs. without LVEF-correlated measurements
- Vision-only versus measurements-only versus fusion (the core E1–E6 ladder)
- Note embedding strategies (ClinicalBERT, MedBERT, bag-of-words)
- 16 versus 32 frames for clip extraction

## Risks and failure modes

- MIMIC-IV-ECHO DICOM storage may approach or exceed local capacity if downloaded indiscriminately.
- View labels are not guaranteed at the clip level in the public metadata.
- Study-level reports and measurements are weak supervision for clip-level generation.
- EchoPrime may be useful only as a baseline feature extractor, not as a preprocessing anchor.
- The laptop is not suitable for serious video-model training beyond tiny pilots.
- Full-scale manuscript experiments likely require Linux plus NVIDIA GPU or cloud compute.

## Privacy and compliance considerations

- use only approved credentialed access paths
- keep raw data on approved local encrypted storage
- preserve original directory structure and checksums
- avoid exporting PHI-bearing artifacts outside the controlled environment
- verify whether note text access is permitted before building report-conditioned workflows

## Milestone boundaries

- Phase 0–3: pipeline and data readiness (COMPLETE)
- Phase 4a: vision-only EchoPrime baseline, 2 clips/study (COMPLETE — test AUC 0.924)
- Phase 4b: multi-video extraction and mean-pooled study embeddings (COMPLETE — test AUC 0.985)
- Phase 4c: tabular-only baseline — structured measurements (excl. LVEF) predicting LVEF (NEXT)
- Phase 5: multimodal fusion — vision + measurements, evaluation ladder comparison
- Phase 6: scale-up — batch-and-purge all 7,000 studies, re-run ladder at full scale
- Phase 7: clinical notes integration (if access confirmed) — E4 and E6 experiments
- Phase 8: manuscript — complete ablation table, figures, and submission

## Resolved questions

- Storage paths: `/restricted/project/mimicecho` (200 GB backed-up) and `/restricted/projectnb/mimicecho` (800 GB non-backed-up). Confirmed by SCC support 2026-03-27.
- Login node: scc4.bu.edu (restricted partition only visible from scc4).
- Per-study storage footprint: ~76 DICOMs/study, ~266 MB raw DICOM/study, ~1.5 MB extracted .npz/study.
- Compute: SCC batch jobs via qsub with `-P mimicecho`. Compute nodes can access `/restricted` paths.

## Resolved questions (Phase 4a+)

- EchoPrime view classifier: confirmed unreliable on MIMIC (69% SSN misclassification due to domain shift). Decision: drop the 11-d view one-hot, use only 512-d encoder features.
- Preprocessing: confirmed our pipeline matches MVE-Echo's reference implementation (crop, mask, color space, normalization).
- DICOM utilization: median 76 DICOMs/study but only ~42 multiframe (cine); initial pipeline used only 2 clips per study; must scale to all clips.

## Open questions

- Do you also have MIMIC-IV Note access for the linked echo reports?
- Which future scale hardware will be available for Phase 5+ fusion training?
- What is the achievable AUC ceiling from structured measurements alone (excl. LVEF)?
- How does multi-video attention aggregation (all clips) compare to mean pooling?
- What is the optimal set of structured measurement features (after LVEF leakage exclusion)?
