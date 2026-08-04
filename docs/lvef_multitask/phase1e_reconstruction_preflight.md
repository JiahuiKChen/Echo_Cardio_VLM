# Phase 1E-A prospective reconstruction preflight

## Decision boundary

Phase 1E-A authorizes construction of a restricted selected-cohort source manifest and an end-to-end technical smoke run only. It does not promote the historical embeddings, authorize the complete C3 rebuild, open confirmatory performance, or alter accepted ASA abstract version 10 or the frozen historical snapshot.

The supplied Phase 1E-A text ends after the first Git command block. The smoke design below therefore uses the narrowest default that exercises every unresolved reconstruction path without opening validation or test subjects. It is versioned and fail-closed. Any increase beyond four studies, 1,000 public DICOM objects, 5 GiB expected raw transfer, train-only scope, two clean runs, or one pinned accelerator requires a new owner decision.

## Prospective authority chain

The future selected-cohort authority is:

`MIMIC-IV-ECHO 1.0 public source object -> exact-object GCS metadata record -> exact-object download and local integrity record -> privacy-safe DICOM/header audit -> multiframe cine candidacy -> deterministic extraction -> pinned encoder-only EchoPrime inference -> unique ordered clip manifest -> stable study-level mean pooling -> complete preservation manifest and independent verification`.

The bucket root does not expose a `SHA256SUMS.txt` authority. Phase 1E-A therefore uses an exact-object `gsutil stat` record as the public-source authority. Before transfer, every expected object must have a recorded remote size, MD5, CRC32C, and generation. After transfer, the local byte count and locally computed MD5 must match the corresponding remote metadata. CRC32C and generation are retained as immutable remote provenance, and a local SHA-256 is computed for every downloaded object and carried into the restricted preservation inventory. Neither GCS MD5 nor CRC32C is represented as a release-provided SHA-256, and unverified SHA-256 fields from historical record manifests are not imported into the prospective authority.

The historical selected-study manifest and split map define cohort membership only. Historical clip embeddings, vectors, labels, measurements, predictions, and performance are not inputs to reconstruction. Historical imaging lineage may be used only to prespecify technical smoke strata and the no-cine negative control.

## Smoke cohort v1

Exactly four distinct selected subjects/studies are chosen from the frozen training split by SHA-256 rank within four imaging-only technical strata:

1. a `batch_000` study represented among the quarantined duplicate groups, with a prespecified normal `batch_000` fallback only if no training candidate exists;
2. a historically cine-positive study from `batch_001` through `batch_008`;
3. a historically cine-positive selected Stage-D study with a surviving extracted-source mapping;
4. one of the three training studies previously classified `NO_MULTIFRAME_CINE_CANDIDATE`, serving as a negative control.

Selection may read selected membership, subject split, component, public record paths/counts, DICOM/read/cine stage flags, source availability, and duplicate-quarantine membership. It must not read structured measurements, LVEF or other labels, predictions, performance metrics, or embedding-vector values. Identifiers and ranks remain restricted. An observed failure does not permit silent replacement; a changed cohort is a new version.

## Pre-download gates

- The branch and starting commit are exact and the SCC worktree is clean.
- Accepted-abstract and frozen-snapshot authorities are unchanged.
- The restricted all-selected source manifest contains exactly 4,530 selected studies and subjects, matches expected object counts per study, has one safe unique MIMIC-IV-ECHO 1.0 relative path per public object, and contains zero outside-selected studies.
- The selected-study, split, historical-study, duplicate-resolution, canonical-inventory, Stage-D, and batch 000–008 inputs must match the SHA-256 authorities locked in the Phase 1E-A config before any row is read. The split must additionally contain exactly 3,171 train, 679 validation, and 680 test subjects.
- The smoke manifest contains exactly four train studies and four subjects, one role apiece, and no outcome-bearing columns.
- The serialized smoke manifest is sealed by SHA-256 across construction, remote preflight, queued execution, environment capture, run-level safety gating, and preservation. A changed manifest blocks before download.
- The remote object set exactly matches the expected object set, and exact-object GCS stat metadata are complete for every object: size, MD5, CRC32C, and generation.
- The smoke scope is no more than 1,000 objects and 5 GiB expected raw bytes, with at least 20 GiB free before transfer.
- The checkpoint is exactly 138,642,379 bytes with SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`.
- Python, executable hash, packages, PyTorch, torchvision, CUDA, cuDNN, GPU, operating system, scheduler identity, source commit, config checksum, command checksum, and script checksums are captured.
- Every row-level manifest and artifact root is outside Git.

## Technical contract

All public DICOM objects for each smoke study are downloaded; there is no within-study sampling. Each observed local object must correspond to exactly one expected safe path, match the exact remote byte count and MD5 returned by GCS, and receive a local SHA-256. The remote CRC32C and generation are preserved with the object record. Missing metadata, missing objects, extra objects, ownership conflicts, size or MD5 disagreement, symlinks, unsafe paths, or unreadable DICOMs fail closed.

Every multiframe candidate is extracted. Extraction requires pydicom 3 raw-pixel decoding, an explicit named plugin for a compressed transfer syntax, exact 8-bit stored pixels, and a supported photometric interpretation. Stored YBR is converted to RGB exactly once; RGB is preserved; monochrome is directly replicated (and MONOCHROME1 inverted) without a YUV/BGR transform. Aggregate-only photometric, transfer-syntax, decoder, and color-transform counts are preserved.

Extraction locks 32 frames, 224 by 224 pixels, three channels, unsigned 8-bit output, deterministic temporal sampling, cubic resizing, stable source-derived clip keys, and explicit ultrasound-mask status. Nonempty-sector, retained-nonzero-pixel, and temporal-variation gates must pass both before and after sampling before a clip can be marked `APPLIED`. A masking or signal-quality failure may never be hidden. Candidate and output ordering may not depend on worker completion order.

EchoPrime inference is encoder-only. It uses the pinned `echo_prime_encoder.pt`, produces one finite 512-dimensional `float32` vector per unique successful clip, verifies authoritative `embedding_idx` alignment and L2 norms, and never loads the view classifier. Study vectors use stable clip-key order, `float64` accumulation, and a `float32` mean-pooled output.

Two independent clean output roots must execute extraction, embedding, and pooling from the same verified DICOM root under the same pinned accelerator and environment. Normalized manifests, extracted internal arrays, clip vectors, and study vectors must agree exactly. NPZ container bytes are not themselves an exactness gate because ZIP metadata can vary; internal array-content hashes are the authority. Cross-accelerator comparison is optional engineering information and cannot qualify or mix an alternate device.

The three positive controls are expected to produce at least one cine, clip embedding, and study vector. The negative control is expected to remain DICOM-readable while producing zero multiframe cines, clip embeddings, and study vectors. Any different result fails the versioned smoke expectation and is investigated without substituting another study.

## Preservation and claim boundary

The restricted preservation pack records safe relative paths, file sizes, local SHA-256, internal array-content hashes, exact GCS object identity and remote size/MD5/CRC32C/generation, source commit, config/command/script checksums, checkpoint identity, environment/hardware/scheduler evidence, timestamps, and aggregate safety status. The serialized restricted downloader report is hash-bound into its aggregate download artifact before any downstream audit. The restricted reproducibility details—which carry logical hashes for both extraction runs, both clip/study manifests, and both clip/study embedding arrays—are likewise hash-bound into the aggregate reproducibility artifact. Immediately before preservation it must reconcile the exact hashes of all 12 aggregate safety inputs, require both restricted-authority bindings, revalidate the saved exact-object metadata and local size-and-MD5 audit, revalidate the smoke-source authority, reread DICOM headers, and recompute all five reproducibility comparisons against their stored restricted and aggregate results. It then snapshots every restricted file and requires that snapshot to remain identical through the complete preservation-manifest scan. An independent second pass must verify the complete inventory, including every local object SHA-256.

A passing smoke run establishes only that the prospective implementation and provenance controls work on the prespecified four-study technical cohort. It cannot establish complete selected-cohort coverage, validate all Stage-D retained NPZs, estimate scientific performance, support modality comparisons, or authorize full C3 execution.

Before full C3 authorization, the multiframe cine-candidacy definition must be adjudicated beyond `NumberOfFrames > 1`, and extraction/embedding must be operationalized in bounded deterministic component batches rather than one cohort-wide in-memory job. The repository-compatible temporal sampler is intentionally not claimed to be identical to the released EchoPrime reference transform; that choice also remains a pre-C3 scientific lock item.
