# Phase 1D embedding go/no-go

## Current ruling

**NO-GO.** The restricted Phase 1D audits are complete enough to rule out deterministic deduplication and the retained-artifact C1/C2 paths. They identify C3 as the only operationally clean prospective path, but do not authorize it. No deduplication, download, re-extraction, re-embedding, pooling, model fitting, outcome access, prediction generation, or confirmatory performance review is authorized.

The historical merged clip and study stores remain unsuitable as confirmatory embedding authority. The audits did not find a vector or normalized-manifest disagreement among the 32 duplicate groups; they found that the physical source evidence needed to prove exact duplication had been purged.

## Observed SCC adjudication

| Gate | Observed state | Evidence and interpretation | Consequence |
|---|---|---|---|
| Duplicate evidence availability | `COMPLETE_LIMITED` | All 32 groups have concordant stored-vector, L2, manifest/extraction-metadata, and component/merged evidence. Source DICOM and extracted NPZ content/hashes are unavailable, so physical-source identity cannot be confirmed. | Vector and metadata concordance are corroborating evidence only. |
| Duplicate adjudication | `SOURCE_ARTIFACT_PURGED` for 32/32 groups | Every duplicate group lacks the retained physical artifacts required to distinguish an exact repeated row, the same clip embedded twice, a key collision, or another source-level defect. | The groups remain quarantined; their scientific cause is unresolved. |
| Physical-source deduplication rule | `FAILED` | No group meets `EXACT_REPEATED_MANIFEST_ROW_CONFIRMED`. Removing only permitted index/L2 serialization differences produces concordance, but does not replace physical/extracted-content confirmation. | Deterministic deduplication and Path A are prohibited. |
| Canonical selected-clip inventory | `COMPLETE_WITH_ATTRITION` | The aggregate inventory accounts for selected and outside-selected rows, physical-source groups, retained NPZs, missing source artifacts, and quarantines. | It can guide a prospective rebuild but cannot promote the historical store. |
| Selected imaging denominator | `NOT_READY_FOR_CONFIRMATORY_USE` | Embedding manifests contain 4,525 selected studies; only 4,524 selected studies retain at least one proposed nonquarantined canonical clip. The five separately prespecified no-cine studies remain without embedded clips. | Exact common-denominator authority must be re-established after a clean rebuild. |
| Extracted/source availability | `PARTIAL` | Stage D retains 14,006 extracted NPZ physical sources. For 170,568 physical sources in `batch_000`–`batch_008`, neither extracted NPZ nor retained DICOM is available. | C1 and C2 are unavailable. Batches 000–008 require authorized DICOM redownload and re-extraction. |
| Checkpoint/environment | `BLOCKED_FOR_EXECUTION` | The intended checkpoint SHA-256 is known, but historical embedding generation is not linked to a complete Python, PyTorch, scikit-learn, CUDA/cuDNN, hardware, source-commit, command, and scheduler record. | Any prospective embedding run must pin and capture a new complete environment. |
| Resource approval | `NOT_REQUESTED` | No SCC quota, transfer, CPU/GPU, queue, or I/O plan has been approved. | C3 cannot begin. |
| Preservation contract | `SPECIFIED_NOT_EXECUTED` | A future authority must use safe relative paths, file sizes and hashes, input/command/config/checkpoint/environment identities, timestamps, and an aggregate safety result. | No new store may be promoted until second-pass preservation verification passes. |
| Owner authorization | `ABSENT` | No written authorization names C3, its inputs, commands, resources, and gate packet. | Every mutating and confirmatory action remains prohibited. |

## Canonical inventory counts

| Quantity | Aggregate count | Interpretation |
|---|---:|---|
| Selected studies seen in embedding manifests | 4,525 | Historical selected-cohort imaging rows exist for these studies. |
| Selected studies with at least one proposed nonquarantined canonical clip | 4,524 | One selected study loses proposed coverage under the current quarantine; this is not yet a locked confirmatory denominator. |
| Selected studies with no embedded clips | 5 | These are the prespecified readable-DICOM/no-multiframe-cine studies and remain imaging-ineligible. |
| Selected embedded manifest rows | 184,606 | Pre-adjudication selected rows. |
| Outside-selected rows excluded | 7,387 | These rows are outside the one-study-per-subject selected cohort and cannot enter the new authority. |
| Selected physical-source groups | 184,574 | Physical-source grouping reduces the selected rows by the 32 duplicated-key groups. |
| Physical sources with surviving extracted NPZ | 14,006 | All are in Stage D and still require cohort-wide readability, schema, and content validation before use. |
| Physical sources with neither NPZ nor retained DICOM | 170,568 | These are in `batch_000`–`batch_008` and require redownload plus re-extraction for a prospective rebuild. |
| Quarantined physical-source groups | 32 | All are `SOURCE_ARTIFACT_PURGED`; none may be deterministically deduplicated. |

Counts are aggregate provenance findings, not authorization to create a canonical row-level manifest. The five no-cine studies remain excluded from every primary vision/structured/fusion comparison; a structured-only full-availability sensitivity, if later authorized, is a different denominator and cannot support paired modality claims.

## Path disposition

| Path | Phase 1D disposition | Reason |
|---|---|---|
| **A. Deterministic deduplication and repooling** | `NOT_COMPATIBLE` | All 32 groups lack physical/extracted-content evidence. Vector and metadata concordance cannot establish exact repeated physical clips. |
| **B. Affected-batch repair** | `NOT_AVAILABLE_AS_RETAINED-SOURCE_REPAIR` | The affected `batch_000` source artifacts were purged. A fresh download would be required, and mixing a newly generated affected batch with incompletely documented legacy batches would not resolve the broader provenance limitation. |
| **C1. Re-embed all selected clips from retained extracted NPZs** | `NOT_COMPATIBLE` | Only 14,006 Stage D NPZ physical sources survive; 170,568 sources have no retained NPZ. Quarantine also remains. |
| **C2. Re-extract all selected clips from retained DICOMs** | `NOT_COMPATIBLE` | Retained DICOMs are unavailable for the 170,568 `batch_000`–`batch_008` physical sources. |
| **C3. Selected-only clean prospective reconstruction** | `OPERATIONALLY_COMPATIBLE_UNAUTHORIZED` | Validate the surviving Stage D NPZ authority, redownload and re-extract the canonical selected sources for `batch_000`–`batch_008`, then re-embed the entire selected imaging-eligible clip set under one pinned checkpoint/environment and rebuild study vectors. |

C3 is a provenance recommendation, not an execution decision. If Stage D NPZ validation fails or cannot establish source mapping, its corresponding DICOMs must also be redownloaded and re-extracted. Raw DICOM retrieval must remain limited to the selected imaging-eligible source set; repeated/all-study expansion is out of scope.

## Aggregate-only packet contract

The duplicate audit may export exactly:

1. `duplicate_clip_evidence_availability.summary.json`;
2. `duplicate_clip_evidence_availability_counts.csv`;
3. `duplicate_clip_adjudication_v2.summary.json`;
4. `duplicate_clip_adjudication_v2_reason_counts.csv`; and
5. `duplicate_clip_adjudication_v2_safety_gate.json`.

The canonical inventory may export exactly:

1. `canonical_selected_clip_inventory.summary.json`;
2. `canonical_selected_clip_inventory_by_component.csv`; and
3. `canonical_selected_clip_inventory_safety_gate.json`.

Per-group identifiers, locators, hashes, frame metadata, vector hashes/norms, extraction metadata, and proposed source rows remain restricted. Only the eight aggregate files may be copied into the review packet after both safety gates pass.

## Required decision inputs before C3 authorization

Before requesting authorization, present:

- the eight safe aggregate audit files and a restricted reviewer attestation;
- a restricted selected-source manifest construction plan that preserves quarantine and the five-study imaging-ineligibility rule;
- Stage D cohort-wide NPZ readability, required-array/schema, content-hash, and source-mapping validation;
- an authorized public-release redownload plan for the selected `batch_000`–`batch_008` sources, with SCC quota, transfer, CPU/GPU, queue, and I/O estimates;
- the source commit, exact commands, extraction specification, checkpoint SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`, environment-capture plan, and preservation-manifest schema; and
- written owner authorization for the exact prospective path.

Until those items are approved and all post-run authority checks pass, the status remains **NO-GO** and confirmatory access remains closed.
