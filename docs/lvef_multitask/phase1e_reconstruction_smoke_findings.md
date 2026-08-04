# Phase 1E-A prospective reconstruction smoke findings

## Authority and safety boundary

- Branch: `codex/lvef-multitask-revalidation`
- Historical base: `23c74ccfd145ab9a423b6942a431a1894a34ab67`
- Phase 1E-A starting authority: `bef7b3f47469b0a9ea54045ba9e3edbc6ca8b242`
- Executed implementation commit: `022d7581eee4cd0278b29c9213e4b65bdc6161b2`
- Execution date: 2026-08-04
- Accepted-abstract authority: version 10, unchanged
- Historical primary binary endpoint: `lvef < 40`, unchanged

This document contains only aggregate-safe findings from the prespecified four-study, training-only technical smoke. The run safety gate passed before aggregate review. No identifier, locator, per-object metadata or hash, DICOM/header row, clip key, array, embedding, environment inventory, scheduler log, label, prediction, or performance value is included in Git.

## Executive disposition

**`PASS_TECHNICAL_SMOKE_ONLY`.** The prospective public-object download, DICOM-header audit, multiframe-candidate pixel decoding and deterministic extraction, pinned encoder-only EchoPrime inference, stable study mean pooling, two-run exact-reproducibility audit, aggregate safety gate, and independent preservation second pass all passed for the frozen four-study smoke cohort.

This result establishes implementation and provenance feasibility on the canary cohort. It does not validate the complete 4,530-study selected cohort, promote the historical embeddings, resolve the 32 purged historical duplicate-key groups, prove equivalence to released EchoPrime preprocessing, authorize full C3 execution, fit a model, generate a prediction, or open confirmatory performance access.

## Prospective selected-source reconstruction

The restricted source builder reconciled the hash-locked historical record rows to the selected studies' recorded DICOM counts before normalizing public-object requests.

| Component | Raw historical record rows | Unique normalized public-object requests | Repeated-locator groups | Collapsed excess rows |
|---|---:|---:|---:|---:|
| Stage D | 24,973 | 24,973 | 0 | 0 |
| Batch 000 | 37,260 | 37,228 | 32 | 32 |
| Batch 001 | 36,225 | 36,225 | 0 | 0 |
| Batch 002 | 37,369 | 37,369 | 0 | 0 |
| Batch 003 | 36,854 | 36,854 | 0 | 0 |
| Batch 004 | 37,173 | 37,173 | 0 | 0 |
| Batch 005 | 37,090 | 37,090 | 0 | 0 |
| Batch 006 | 36,979 | 36,979 | 0 | 0 |
| Batch 007 | 37,384 | 37,384 | 0 | 0 |
| Batch 008 | 14,709 | 14,709 | 0 | 0 |
| **Total** | **336,016** | **335,984** | **32** | **32** |

All 32 repeated public-object locator groups had multiplicity two and identical authority fields; no locator, ownership, component, key, or recorded-size conflict was found. The resulting prospective source-request manifest candidate contains exactly 4,530 selected subjects/studies and zero outside-selected studies. The four smoke studies were selected from the 3,171-subject training split without reading outcomes, predictions, embedding values, or performance.

Phase 1E-A obtained exact prefix-listing and GCS stat authority only for the 252 smoke objects. The remaining 335,732 normalized public-object requests were not externally listed, statted, or downloaded in this phase. Their full-cohort public existence, size, and integrity therefore remain unverified and must fail closed before full C3 execution.

These 32 source-row reconciliations are a different construct from the 32 quarantined historical clip-key groups. They are not retrospective clip deduplication, do not prove cross-construct equality, and do not restore historical embedding authority.

## Exact-object preflight and transport

| Gate | Aggregate finding |
|---|---:|
| Smoke subjects/studies/technical roles | 4 / 4 / 4 |
| Exact public objects | 252 |
| Expected and downloaded bytes | 1,124,722,942 (1.05 GiB) |
| Remote metadata authority | `GCS_EXACT_OBJECT_STAT` |
| Complete remote size/MD5/CRC32C/generation records | 252 / 252 |
| Local MD5 verified and local SHA-256 computed | 252 / 252 |
| Remote-metadata, size, MD5, local-hash, missing, unexpected, or symlink failures | 0 |

The prefix listing, exact-object stat set, and requested object set agreed before download. Every object was newly downloaded to the fresh run root, matched its remote byte count and MD5, received a local SHA-256, and passed the independent downloaded-object audit. The bucket root was not represented as providing a release SHA-256 authority.

## DICOM-header and multiframe audit

All 252 downloaded objects were DICOM-header readable with `stop_before_pixels=True`. The audit found 123 multiframe candidates and 129 single-frame objects across four subjects/studies. Each of the three positive-control studies had at least one multiframe candidate, while the prespecified historical no-multiframe-candidate training study remained header-readable and had zero multiframe candidates. Both control gates passed. Only the 123 multiframe candidates were subsequently pixel-decoded and extracted.

All header-readable objects reported `YBR_FULL_422` photometric interpretation and JPEG Baseline transfer syntax `1.2.840.10008.1.2.4.50`. These are technical format counts for this canary cohort, not a cohort-wide format characterization.

## Extraction, EchoPrime inference, and pooling

Two independent clean roots executed the same frozen pipeline on one allocated accelerator.

| Stage | Run A | Run B | Required gate |
|---|---:|---:|---|
| Requested and successfully pixel-decoded/extracted multiframe candidates | 123 / 123 | 123 / 123 | No failures and no duplicate clip keys |
| Studies with extracted multiframe candidates | 3 | 3 | Exact three positive controls |
| Clip embeddings | 123 | 123 | Finite `float32`, width 512, authoritative index/hash/L2 reconciliation |
| Study vectors | 3 | 3 | Stable clip order, `float64` accumulation, `float32` mean-pooled output |

Every extracted multiframe candidate had exact shape `32 x 224 x 224 x 3`, `uint8` dtype, an explicit applied mask, and passing source/sample signal and temporal-variation gates. All 123 candidates used the recorded `pydicom_pixels_raw:pillow` decoder path and explicit `YBR_FULL_422`-to-RGB conversion. The temporal policy was `historical_compatible_linspace_or_tail_repeat_v1`; it remains a repository-compatible custom transform and is not claimed to equal the released EchoPrime reference preprocessing.

Inference used the pinned 138,642,379-byte checkpoint with SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`, CUDA, PyTorch `2.11.0+cu130`, and torchvision `0.26.0+cu130`. The encoder alone was loaded with strict state-dictionary compatibility; the view classifier was not loaded. Exact new-run file identity was established, but independent official-release provenance and historical use remain unproven. The full software, CUDA/cuDNN, hardware, scheduler, command, config, and package record remains restricted and checksum-preserved.

## Exact reproducibility and preservation

The reproducibility audit compared the exact required five cross-run artifact pairs: the normalized clip and study manifests, the clip and study embedding arrays, and the extraction run using internal array-content hashes. All 5/5 comparisons were exact, with zero unequal pairs. The extraction comparison reconciled 369 internal arrays, three per multiframe candidate. NPZ container bytes were deliberately not used as an exactness gate.

The aggregate safety gate passed over exactly 12 aggregate inputs and recorded that no model was fitted, no prediction was generated, and no confirmatory performance was accessed.

The independent preservation second pass also passed:

- 566 files and 1,729,369,859 bytes (1.61 GiB) were inventoried;
- the exact file set, sizes, SHA-256 values, and no-symlink gate all passed;
- source commit, config, checkpoint, environment, scheduler/job, command, and aggregate-safety identities were recorded;
- aggregate artifact hashes, the download and DICOM audits, all reproducibility comparisons, and the restricted snapshot were independently recomputed and reconciled; and
- the preservation manifest, metadata, and verification SHA-256 values are `7be4f39e7ce9123d3f59407432cd39257082f13ceebbca8390266979f81fcc8b`, `a2d33af15c99ffd12bfbe7e3728cf6f4fc492146f58f2c59e2cfb6f93cdc66f7`, and `4548d55a1f2e0a777a09f1068700ce7a89f8d264d193fddf2c09011da3965938`, respectively.

## Gate effects

| Gate | Phase 1E-A disposition | What remains blocked |
|---|---|---|
| Selected-source request manifest candidate | `PASS_STRUCTURAL_RECONCILIATION_ONLY` | Exact-object GCS preflight for the remaining 335,732 requests, full-cohort download, and reconstructed clip authority |
| Exact-object preflight and smoke download | `PASS_FOR_SMOKE` | Full C3 resource and owner authorization |
| DICOM-header/multiframe audit | `PASS_FOR_SMOKE` | Cohort-wide cine-usability adjudication beyond `NumberOfFrames > 1` |
| Extraction reproducibility | `PASS_FOR_SMOKE` | Production component batching and final preprocessing lock |
| Encoder reproducibility | `PASS_FOR_SMOKE` | Full selected-cohort embedding authority |
| Study pooling reproducibility | `PASS_FOR_SMOKE` | Complete imaging-eligible denominator and study inventory |
| Preservation second pass | `PASS_FOR_SMOKE` | Full-run preservation authority |
| Historical duplicate and embedding authority | `BLOCKED` | The 32 groups remain `SOURCE_ARTIFACT_PURGED`; historical store remains unusable |
| Full C3 execution | `NOT_AUTHORIZED` | Scope/resources, preprocessing, batching, provenance contract, and written owner authorization |
| Confirmatory modeling/access | `NOT_AUTHORIZED` | Full embedding authority, Phase 1E-B clinical metadata/panels, denominator/SAP/config locks, and separate owner authorization |

## Remaining decision boundary

Before full C3 can be considered, the project must adjudicate cine candidacy beyond `NumberOfFrames > 1`, freeze the temporal/reference-transform and decoder/color/mask/quality specification, implement bounded deterministic production components with retry/resume and quota controls, decide whether Stage-D sources can be reused after cohort-wide validation or must be redownloaded, approve the approximately 1.2-TB resource envelope, and issue written owner authorization naming the commit, config, checkpoint, scope, commands, and preservation contract.

Clinical metadata and panel adjudication are not needed to preserve outcome-blind imaging reconstruction, but they remain mandatory before model fitting. Phase 1E-B must separately establish or block the LVEF authority, complete the nine technical reviews, obtain echocardiographer signoff on the eight clinical questions, finalize leakage families and task panels, and preserve the existing `NOT_GENERATED_ZERO_LITERATURE_ANSWERABLE_AMBIGUITIES` OpenEvidence disposition unless new project evidence changes that classification.
