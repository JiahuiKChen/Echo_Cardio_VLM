# PRD: MIMIC-IV-ECHO Generative Echocardiography MVP

## Project title

Storage-aware echocardiography video representation and reconstruction on MIMIC-IV-ECHO, with a bridge to future conditional generation

## Executive summary

The project goal is to build a rigorous, reproducible echocardiography AI pipeline on top of MIMIC-IV-ECHO that starts with the smallest scientifically defensible path to a publishable result and preserves a clean path toward later report- or measurement-conditioned generation. The initial product is not a full text-to-video generator. It is a storage-aware data and experimentation stack that can: ingest MIMIC-IV-ECHO study metadata and selected DICOM studies, build a deterministic study and clip manifest, extract normalized cine clips, validate a pretrained baseline such as EchoPrime for encoder reuse, and train a first echo-specific reconstruction model on a carefully chosen subset. The immediate target is a working end-to-end pipeline and a credible reconstruction result on a constrained subset, followed by evaluation strong enough to support a future manuscript.

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

## Current state of the field

Three literature signals matter most for this project:

### EchoCardMAE implication

Echo-specific masked video modeling is a credible route to useful echo representations and reconstructions. The paper is important not because it solves generation, but because it shows that echo-tailored reconstruction choices matter:

- key-region masking rather than generic masking
- robustness to temporal clip selection
- denoising-aware reconstruction under ultrasound speckle

That supports a reconstruction-first MVP rather than jumping straight to a full conditional generative model.

### Nature CXR generation implication

The chest X-ray paper shows what a technically credible medical generation paper looks like:

- start from a strong pretrained base and adapt to the domain
- evaluate fidelity, diversity, prompt alignment, expert judgment, retrieval/report consistency, and downstream synthetic utility
- separate image realism from clinical correctness
- show synthetic utility as augmentation, not just pretty samples

For echo, this means we should not publish around visuals alone. We need an evaluation stack that tests anatomical plausibility, view consistency, structured-measurement consistency, expert readability, and downstream usefulness.

### AI echocardiography review implication

The review reinforces that:

- multi-view echo interpretation is valuable but operationally complex
- vision-language work such as EchoPrime exists, but large-scale public multimodal echo resources are limited
- future progress depends on warehousing, standardization, and multimodal linkage

That argues for investing early in a robust manifest and preprocessing layer.

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
- Validate raw DICOM ingestion on a small study subset.
- Produce a reproducible clip extraction and QC pipeline.
- Establish one strong baseline feature extractor / retrieval baseline.
- Train and evaluate one echo-specific reconstruction-first baseline on a subset.
- Preserve a clean migration path to larger hardware and later conditional generation.

## Non-goals

- Training a large direct text-to-video model on the laptop.
- Downloading all imaging data before subset validation.
- Treating EchoPrime as the main training codebase.
- Claiming clinical deployment readiness.

## Hypotheses

- H1: A reconstruction-first masked video objective on filtered MIMIC-IV-ECHO clips will produce clinically interpretable reconstructions and reusable embeddings with lower risk than direct conditional generation.
- H2: Restricting the first experiment to a dominant view such as A4C will reduce engineering and evaluation noise enough to accelerate the first publishable result.
- H3: A deterministic manifest plus derived normalized clip store will provide better long-term flexibility than trying to use EchoPrime as a compression layer.
- H4: Study-level structured measurements can later support conditional or controllable generation, but they are too weakly aligned for the first MVP.

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

- first model: small masked reconstruction / MAE-style baseline on filtered clips
- later model: study- or clip-conditioned latent generation

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

### Milestone 4

- first reconstruction model produces stable training and visually credible outputs on a filtered view subset

### Milestone 5

- evaluation table shows incremental value across the modality-isolation ladder (E1–E6)

## Evaluation strategy: modality isolation

A core principle of this project is strict modality isolation in the evaluation stack.
Each source of information (vision, structured measurements, clinical notes) must be
evaluated independently before any multimodal combination is tested. This prevents
data leakage, enables clean ablation tables, and produces the layered contribution
narrative that journal reviewers expect.

### Evaluation ladder (each row is a distinct experiment)

| Row | Input modality | Model | What it proves |
|-----|---------------|-------|---------------|
| E1 | Raw keyframe pixels | PCA + Ridge | Floor baseline (completed: test AUC ≈ 0.37) |
| E2 | EchoPrime 523-d clip embeddings | Ridge | **Completed: test AUC = 0.924** — pretrained features capture function from vision alone |
| E3 | Learned reconstruction embeddings | Ridge / LogReg | Are our trained representations competitive with EchoPrime? |
| E4 | Structured measurements only (excl. LVEF) | Ridge / LogReg | Tabular ceiling — how far can non-imaging data go? |
| E5 | Vision embeddings + measurements | Fusion model | Does multimodal fusion add incremental value? |
| E6 | Vision + measurements + notes | Fusion model | Full multimodal contribution |

Rows E1–E3 are vision-only and must be completed before any multimodal experiment.
Structured measurements must never be included as input features when LVEF is the target
without explicitly excluding LVEF and LVEF-derived fields from the input, to avoid leakage.

### Role of structured measurements (deferred to Phase 6+)

Structured measurements are most valuable as:
- **Evaluation signal**: Do reconstructed echoes produce anatomy consistent with original measurements?
- **Conditioning input**: Generate echoes conditioned on target measurements (controllable generation).
- **Retrieval evaluation**: Can learned embeddings retrieve studies with similar measurement profiles?
- **Tabular ceiling**: Row E4 establishes an upper bound for non-imaging prediction.

They should NOT be used as input features in Phase 4 (baseline) or Phase 5 (reconstruction)
experiments. This preserves the clean ablation story and avoids the leakage concern.

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

- single-view versus multi-view training
- 16 versus 32 frames
- 112 versus 224 resolution
- with versus without ultrasound-region masking
- plain masked reconstruction versus echo-specific masking heuristics
- with versus without denoised targets

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
- Phase 4a: vision-only EchoPrime baseline (COMPLETE — test AUC 0.924)
- Phase 4b: tabular-only measurement baseline (deferred, provides ceiling)
- Phase 5: reconstruction-first experiment
- Phase 6: multimodal integration and evaluation ladder (measurements + notes as inputs)
- Phase 7: decision gate on scale-up or pivot
- Phase 8: manuscript framing and scale-up planning

## Resolved questions

- Storage paths: `/restricted/project/mimicecho` (200 GB backed-up) and `/restricted/projectnb/mimicecho` (800 GB non-backed-up). Confirmed by SCC support 2026-03-27.
- Login node: scc4.bu.edu (restricted partition only visible from scc4).
- Per-study storage footprint: ~76 DICOMs/study, ~266 MB raw DICOM/study, ~1.5 MB extracted .npz/study.
- Compute: SCC batch jobs via qsub with `-P mimicecho`. Compute nodes can access `/restricted` paths.

## Open questions

- Do you also have MIMIC-IV Note access for the linked echo reports?
- How many clean A4C-like clips remain after view filtering on MIMIC-IV-ECHO? (EchoPrime view classifier will answer this)
- Which future scale hardware will be available for Phase 5 reconstruction training?
- What is the achievable AUC ceiling from structured measurements alone (excl. LVEF)?
