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

### Milestone 1

- metadata and manifest pipeline runs end to end on a pilot subset
- DICOM ingestion succeeds on a nontrivial sample
- clip normalization is deterministic

### Milestone 2

- EchoPrime smoke test is completed or ruled out with concrete blocker documentation
- study and clip counts after QC are known

### Milestone 3

- first reconstruction model produces stable training and visually credible outputs on a filtered view subset

### Milestone 4

- evaluation table shows at least one meaningful utility signal beyond image aesthetics

## Offline evaluation plan

Adapt the evaluation philosophy of the Nature CXR paper, but make it echo-specific.

### For reconstruction

- pixel-space metrics: MSE, MAE, PSNR, SSIM
- latent or feature-space reconstruction similarity using a frozen encoder
- view consistency score from a frozen view classifier
- structured-measurement prediction agreement where feasible
- clinician-style qualitative review of view identity and major anatomy

### For representation utility

- linear probe or lightweight regressor on structured measurements
- retrieval of nearest clips or studies by diagnosis-related measurements
- optional downstream task transfer on public datasets if format alignment is manageable

### For later generation

- fidelity and diversity metrics adapted to echo
- expert reader study
- view-conditional correctness
- measurement-conditioned consistency
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

- Phase 0-3 are pipeline and data readiness
- Phase 4 is the first meaningful model baseline
- Phase 5 is the first reconstruction-first experiment
- Phase 6 is the decision gate on whether to scale or pivot
- Phase 7 is manuscript framing and scale-up planning

## Open questions

- Do you also have MIMIC-IV Note access for the linked echo reports?
- What exact mount path and write permissions are stable for the external drive from the execution environment?
- How many clean A4C-like clips remain after view filtering on MIMIC-IV-ECHO?
- What is the actual per-study storage footprint after pilot download?
- Which future scale hardware will be available first: Linux workstation, cloud GPU, or lab server?
