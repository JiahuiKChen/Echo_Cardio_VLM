# Echo AI Project Plan

## Phase 0: Literature synthesis and repo audit

### Objectives

- ground the project in the most relevant echo and medical generation literature
- verify what EchoPrime actually provides

### Tasks

- summarize EchoCardMAE, the Nature CXR generation paper, and the AI echocardiography review
- clone and audit EchoPrime code and release assets
- test environment assumptions on Apple Silicon

### Deliverables

- literature framing
- EchoPrime audit
- initial smoke-test scripts

### Dependencies

- local workspace
- internet access for repo and official docs

### Risks

- overestimating EchoPrime capabilities
- designing around CUDA-only assumptions

### Exit criteria

- a documented recommendation for the default MVP
- a documented EchoPrime smoke-test path

### Reproducibility artifacts

- repo commit hash
- release asset sizes and URLs
- environment notes

## Phase 1: Metadata inspection and subset selection

### Objectives

- inspect real metadata before any major imaging download
- choose the smallest subset that supports the next decision

### Tasks

- download metadata tables only
- validate filenames, schemas, row counts, and join keys
- compute per-study DICOM counts
- measure linkage rates to measurements and notes
- define Stage B/C/D acquisition lists

### Deliverables

- metadata summary
- study-level manifest
- first download candidate list

### Dependencies

- access to metadata tables

### Risks

- filename and schema mismatches between public docs and actual access page
- note linkage present but note text unavailable

### Exit criteria

- a pilot study list exists and has known expected storage and linkage properties

### Reproducibility artifacts

- metadata checksums
- raw header snapshots
- manifest generation script
- summary JSON and CSV outputs

## Phase 2: Local environment and smoke tests

### Objectives

- verify that the local machine can execute the basic tooling
- distinguish laptop-feasible tasks from deferred scale tasks

### Tasks

- create a clean minimal EchoPrime environment
- test imports and backbone forward pass
- verify model asset presence
- run encoder-only smoke test
- document blockers for full inference on M2 if any

### Deliverables

- working venv recipe
- smoke-test logs
- hardware classification notes

### Dependencies

- Python environment
- optional release asset download

### Risks

- repo requirements break on macOS
- full EchoPrime inference requires too much memory or CPU time

### Exit criteria

- either a successful smoke test or a precise blocker list with mitigation

### Reproducibility artifacts

- `pip freeze`
- smoke-test JSON output
- environment setup script

## Phase 3: Preprocessing and data manifest pipeline

### Objectives

- turn raw MIMIC-IV-ECHO studies into deterministic clip-level training inputs

### Tasks

- implement DICOM study walker
- extract clip metadata
- normalize frame count and spatial size
- emit study and clip manifests
- add QC fields such as decode success, frame count, inferred view, and checksum

### Deliverables

- study manifest
- clip manifest
- normalized pilot clip cache
- QC report

### Dependencies

- pilot study download
- metadata joins

### Risks

- vendor-specific DICOM decode issues
- view imbalance or insufficient clean A4C clips

### Exit criteria

- pilot subset is fully ingestible and queryable by manifest

### Reproducibility artifacts

- deterministic preprocessing config
- raw-to-derived mapping table
- per-study logs

## Phase 4a: Vision-only EchoPrime baseline (COMPLETE — 2026-03-29)

### Objectives

- prove that pretrained echo video features capture cardiac function from vision alone
- establish a strong vision-only baseline before any multimodal experiments
- strict modality isolation: no structured measurements as input features

### Tasks

- stage EchoPrime weights (echo_prime_encoder.pt, view_classifier.pt) to SCC
- extract 523-d embeddings (512 video + 11 view) for all Stage D clips
- train Ridge regression (LVEF continuous) and LogReg (LVEF binary) on embeddings
- compare test AUC/MAE/R² against the raw-pixel floor (E1 vs E2 in evaluation ladder)
- analyze view distribution from the classifier output

### Deliverables

- clip embedding NPZ and manifest
- embedding-based LVEF baseline metrics (train/val/test)
- view distribution summary across 489 studies
- comparison table: raw pixels vs EchoPrime embeddings

### Dependencies

- Stage D extracted clips (COMPLETE)
- LVEF still manifest with train/val/test splits (COMPLETE)
- EchoPrime pretrained weights on SCC

### Risks

- weak utility due to mismatch between public MIMIC data and EchoPrime's training distribution
- CUDA memory pressure if batch size is too large

### Exit criteria

- EchoPrime embedding test AUC significantly above 0.50 (the raw-pixel floor is 0.37)
- if AUC > 0.65: strong signal, proceed to Phase 5
- if AUC 0.50–0.65: moderate signal, investigate view filtering before Phase 5
- if AUC < 0.50: weak signal, reassess data quality or EchoPrime applicability

### Result (2026-03-29)

- **Test AUC = 0.924** — exceeds the 0.65 threshold for "strong signal"
- Study-level: AUC 0.924, AP 0.797, R² 0.446, MAE 8.4, RMSE 10.2
- Val AUC 0.915, minimal overfitting
- 998/998 clips embedded, 489 studies with LVEF labels joined
- Decision: **proceed to Phase 5** (reconstruction experiment)

### Reproducibility artifacts

- embedding extraction config and manifest
- baseline predictions CSV
- metrics JSON
- view distribution summary

## Phase 4b: Tabular measurement ceiling (deferred)

### Objectives

- establish an upper bound for what structured measurements alone can predict
- provide the "ceiling" row (E4) for the evaluation ladder

### Tasks

- extract non-LVEF structured measurements as tabular features
- train Ridge/LogReg on measurement features → LVEF
- compare against vision-only results from Phase 4a
- this is a control experiment, not a main contribution

### Exit criteria

- tabular ceiling AUC and MAE are known for comparison in the manuscript

## Phase 5: First reconstruction or generation experiment

### Objectives

- train the first echo-specific model aligned with the eventual generation goal

### Tasks

- start with single-view masked reconstruction on a filtered subset
- use a small MAE-style or masked autoencoder baseline
- log reconstructions, validation curves, and ablations

### Deliverables

- trained pilot reconstruction model
- reconstruction figure panels
- ablation summary

### Dependencies

- stable clip cache
- baseline verification from Phase 4

### Risks

- training too slow on laptop
- reconstructions look plausible but not clinically meaningful

### Exit criteria

- stable training and at least one credible qualitative and quantitative reconstruction result

### Reproducibility artifacts

- model config
- checkpoints
- sample reconstructions
- training logs

## Phase 6: Evaluation and decision gate

### Objectives

- decide whether to scale, pivot, or stop

### Tasks

- compute reconstruction metrics
- test view consistency and measurement consistency
- run a small expert-style review protocol if feasible
- compare against baseline and ablations

### Deliverables

- evaluation table
- go/no-go decision memo

### Dependencies

- Phase 5 outputs

### Risks

- metrics fail to reflect clinical plausibility
- insufficient sample size for strong claims

### Exit criteria

- a clear recommendation on scale-up or redesign

### Reproducibility artifacts

- frozen evaluation code
- summary tables
- figure manifests

## Phase 7: Scale-up plan and manuscript framing

### Objectives

- translate the pilot into a paper-quality program

### Tasks

- estimate compute and storage for larger subsets
- choose scale hardware
- plan manuscript claims, figures, and evaluation scope
- decide when to introduce conditional generation

### Deliverables

- scale-up spec
- manuscript outline
- figure and table plan

### Dependencies

- successful decision gate

### Risks

- overextending from a pilot result
- scaling before data quality is understood

### Exit criteria

- a defensible plan for larger training and paper framing

### Reproducibility artifacts

- hardware plan
- frozen subset definition
- manuscript checklist

## Hardware classification

### Laptop-feasible now

- metadata inspection
- manifest generation
- checksum verification
- DICOM decode on tiny subsets
- EchoPrime environment setup
- encoder-only smoke tests
- tiny preprocessing and tiny pilot runs

### Laptop-feasible but slow

- limited clip extraction on a few hundred studies
- tiny reconstruction experiments at low resolution

### Not realistic on current hardware

- serious multi-view video pretraining
- large-scale direct conditional generation
- manuscript-scale diffusion or large transformer video training

### Better deferred to scale hardware

- reconstruction pretraining on large subsets
- any direct conditional generation model
- broad ablation sweeps and repeated experiments
