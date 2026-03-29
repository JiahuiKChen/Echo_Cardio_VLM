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

## Phase 4b: Multi-video extraction and aggregation (COMPLETE — 2026-03-29)

### Objectives

- extract EchoPrime 512-d embeddings from ALL multiframe DICOMs per study (not just 2)
- build study-level aggregated embeddings (attention pooling or mean pooling)
- validate H3: multi-video aggregation improves over 2-clip baseline

### Tasks

- modify extraction pipeline to process all cine DICOMs per study (current DICOM audit shows median ~42 multiframe DICOMs/study across our 500 studies)
- drop the 11-d view one-hot features (unreliable); use only 512-d encoder features
- implement study-level aggregation: start with mean pooling, then attention-weighted pooling
- re-run LVEF baseline with study-level multi-video embeddings
- compare study-level AUC: 2-clip vs all-clip

### Deliverables

- all-clip embedding NPZ and manifest (~21,000 clips for 500 studies)
- study-level aggregated embedding NPZ (one 512-d vector per study)
- comparison table: E2a (2-clip) vs E2b (all-clip, mean pool) vs E2b (all-clip, attention)

### Dependencies

- Stage D extracted clips — must re-run extraction for ALL multiframe DICOMs, not just 2/study
- EchoPrime encoder weights (already on SCC)

### Risks

- Extraction time scales linearly: ~21K clips takes ~10x longer than 998 clips
- GPU memory: batch processing should be fine (83s for 998 clips at bs=8)
- Storage: ~21K NPZ files at ~1.5 MB each ≈ 32 GB in projectnb (within quota)

### Exit criteria

- All-clip study-level AUC ≥ E2a AUC (0.924) — validates multi-video value
- If AUC improves: confirms H3, proceed with all-clip for remaining experiments
- If AUC unchanged or worse: mean pooling is sufficient, consider simpler aggregation

### Result (2026-03-29)

- **Test AUC = 0.985** (up from 0.924), AP 0.942, R² 0.588, MAE 6.7 EF pts
- 21,393 clips extracted → 512-d encoder-only embeddings → mean-pooled to 499 study-level vectors
- Median 42.9 clips/study (range 18–65), matching MVE-Echo's median of 41
- H3 strongly validated: +0.061 AUC, -1.7 MAE by using all clips instead of 2
- Embedding extraction took 867s (14.5 min) on GPU, aggregation + baseline on CPU in seconds
- Decision: **proceed to Phase 4c** (tabular measurement baseline)

### Reproducibility artifacts

- all-clip extraction manifest
- aggregation method code and config
- comparison metrics JSON

## Phase 4c: Tabular measurement baseline (COMPLETE — 2026-03-29)

### Objectives

- establish what structured measurements alone can predict (E3 in evaluation ladder)
- identify which measurements are safe to use as input features (LVEF leakage audit)

### Tasks

- audit all 114 unique measurements: identify any LVEF-derived or LVEF-correlated fields
- build a feature matrix from non-LVEF structured measurements (handle missing values)
- train Ridge/LogReg on measurement features → LVEF (binary and continuous)
- report AUC, MAE, R² to compare against vision-only (E2b)

### Deliverables

- measurement feature matrix with leakage audit report
- tabular-only LVEF baseline metrics
- feature importance ranking

### Exit criteria

- tabular ceiling AUC is known for comparison in the fusion experiment

### Result (2026-03-29)

- **Test AUC = 0.947**, AP 0.805, R² 0.425, MAE 8.6 EF pts
- 39 features retained from 60 total (3 excluded as LVEF leakage, 18 low-coverage)
- 26% mean missing rate, handled via median imputation
- Top features: lvesd (-4.49), lvot_vti (+4.45), sept_e_prime (+5.29), lvedd (-3.49)
- Vision (0.985) > tabular (0.947): clear room for fusion to potentially improve
- Decision: **proceed to Phase 5** (multimodal fusion)

## Phase 5: Multimodal fusion (COMPLETE — 2026-03-29)

### Objectives

- combine vision embeddings with structured measurements (novel contribution)
- demonstrate incremental value of multimodal input (E5 in evaluation ladder)

### Tasks

- concatenate study-level vision embeddings (512-d) with tabular measurement features
- train linear (Ridge/LogReg) and MLP fusion models
- compare fusion vs vision-only vs measurements-only with bootstrap CIs
- controlled 3-way comparison with identical model architectures

### Result (2026-03-29)

Linear models (test set, n=84):

| Config | Test AUC | 95% CI | MAE | R² |
|--------|----------|--------|-----|-----|
| Vision only | 0.985 | [0.947, 1.0] | 6.70 | 0.588 |
| Tabular only | 0.947 | [0.867, 0.999] | 8.63 | 0.425 |
| Fusion | 0.990 | [0.963, 1.0] | 7.27 | 0.560 |

Key findings:
- Fusion edges vision by +0.005 AUC but CIs overlap → not significant at n=84
- Fusion MAE worse than vision-only → tabular adds noise to continuous regression
- MLPs overfit badly (tabular MLP AUC 0.664, negative R²) → need more data, not more model
- Decision: **proceed to Phase 6 scale-up** for statistical power

## Phase 6: Full-scale extraction (batch-and-purge) (NEXT)

### Objectives

- scale from 500 to all ~7,000 MIMIC-IV-ECHO studies for publication-quality statistics
- achieve sample sizes comparable to published work (MVE-Echo: 7,169 studies)

### Tasks

- batch-and-purge workflow: download ~500 studies, extract all clips, compute embeddings, delete raw DICOMs, repeat
- storage budget: ~266 MB × 500 = ~130 GB per batch (within 800 GB projectnb)
- final stored artifacts: embeddings + manifests only (~1-2 GB total)
- re-run full evaluation ladder (E1–E5) at 7,000-study scale
- compute confidence intervals and statistical significance tests

### Deliverables

- full-scale embedding store for all 7,000 studies
- publication-quality evaluation table with CIs
- batch-and-purge execution logs

### Dependencies

- Phase 5 fusion pipeline validated on 500 studies

### Exit criteria

- all studies processed, final evaluation table ready for manuscript

## Phase 7: Clinical notes integration

### Objectives

- add clinical notes as a third modality (E4 and E6 in evaluation ladder)
- requires confirmed MIMIC-IV Note access

### Tasks

- verify note access and link echo studies to discharge/radiology notes
- extract note embeddings (ClinicalBERT or similar)
- E4: notes-only → LVEF baseline
- E6: vision + measurements + notes → LVEF fusion
- compare across full E1–E6 ladder

### Exit criteria

- full E1–E6 ladder complete with all three modalities

## Phase 8: Manuscript

### Objectives

- write and submit the publication

### Tasks

- finalize ablation table (E1–E6)
- create manuscript figures: modality contribution, attention heatmaps, case studies
- frame contribution: first systematic multimodal evaluation on public echo data
- compare against MVE-Echo, Echo-Vision-FM, EchoPrime baselines
- discuss limitations (domain shift, note access, storage constraints)

### Exit criteria

- submitted manuscript with reproducible code and data pipeline

## Hardware classification (SCC-based)

### CPU batch jobs (no GPU needed)

- metadata inspection and manifest generation
- DICOM download (gsutil, 4-6h for 500 studies)
- DICOM audit and cine extraction
- structured measurement export from BigQuery
- feature matrix construction and tabular baselines (Ridge/LogReg)

### GPU batch jobs (A40/A6000, gpu_c=8.0, gpu_memory=48G)

- EchoPrime embedding extraction (~83s for 998 clips at bs=8)
- Multi-video extraction: ~21K clips/500 studies, estimate ~30 min at bs=8
- Fusion MLP training (lightweight, any GPU suffices)

### Full-scale batch-and-purge (CPU + GPU per batch)

- ~14 batches of 500 studies each
- ~130 GB raw DICOM per batch (within 800 GB projectnb quota)
- Delete raw DICOMs after embedding extraction, keep only NPZ + manifests
- Total timeline estimate: 2-3 days of queued jobs

### Not needed for this project

- Multi-GPU training (all models are lightweight MLPs on frozen embeddings)
- VideoMAE pre-training (Echo-Vision-FM already published this)
