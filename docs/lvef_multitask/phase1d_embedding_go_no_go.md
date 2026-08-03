# Phase 1D embedding go/no-go

## Current ruling

**NO-GO.** Phase 1D authorizes only model-independent restricted audits of duplicate evidence and canonical selected-clip availability. It does not authorize deduplication, copying or rebuilding arrays, re-extraction, re-embedding, pooling, download, outcome access, model fitting, prediction generation, or confirmatory performance review.

## Required gate record

| Gate | Current state | Passing evidence | Consequence while unresolved |
|---|---|---|---|
| Duplicate evidence availability | `PENDING_SCC` | `duplicate_clip_evidence_availability.summary.json` and counts show required locator, file, manifest-hash, computable-hash, frame, extraction, ownership, payload, L2, and component/merged evidence for all 32 groups | No duplicate resolution may be accepted |
| Duplicate adjudication | `PENDING_SCC` | Exactly 32 restricted groups receive one requested v2 category; aggregate reason counts and safety gate pass | Historical merged/study embeddings remain blocked |
| Physical-source deduplication rule | `LOCKED_FAIL_CLOSED` | Only `EXACT_REPEATED_MANIFEST_ROW_CONFIRMED` can permit deterministic deduplication; that category requires equality after removing only permitted index/L2 rewrites, confirmed physical-source identity, concordant extraction metadata, equal extracted NPZ/frame content evidence, vector/L2 agreement, and merged-component correspondence. `MERGE_OR_INDEX_REWRITE_ONLY` permits only a merge rebuild. Vector equality alone never permits deduplication | Path A unavailable for every group lacking physical/extracted confirmation |
| Canonical selected clip inventory | `PENDING_SCC` | Ten component rows (`stage_d`, `batch_000`–`batch_008`) account for selected, outside-selected, ownership-mismatch, deduplicated, proposed, and quarantined rows | No selected-only source authority |
| Selected imaging denominator | `PENDING_SCC_RECHECK` | Exactly 4,525 selected imaging-eligible studies have at least one nonquarantined proposed canonical clip; five no-cine studies remain outside every primary modality | No common vision/structured/fusion denominator |
| Extracted clip availability | `PENDING_SCC` | Aggregate inventory distinguishes NPZ file existence, retained DICOM without NPZ, and neither for every proposed physical source. File existence is only C1 compatibility evidence; cohort-wide NPZ readability, required-array/schema, and content-hash validation remains a separate pre-execution gate | C1/C2/C3 cannot be selected |
| Re-extraction/redownload scope | `PENDING_SCC` | Component counts establish whether no re-extraction, only `batch_000`, multiple/all batches, or redownload is required | Storage and scheduler request cannot be finalized |
| Checkpoint/environment | `BLOCKED_FOR_EXECUTION` | Chosen path pins checkpoint SHA-256, source commit, Python/packages, PyTorch, CUDA/cuDNN, GPU, command, and scheduler identity before execution | No embedding generation or mixed-store promotion |
| Resource approval | `NOT_REQUESTED` | SCC free-space/quota, CPU/GPU, queue, transfer, and I/O plan approved for one of A/B/C1/C2/C3 | No execution |
| Preservation contract | `SPECIFIED_NOT_EXECUTED` | Safe-relative-path manifests include file sizes/hashes, source/command/config/checkpoint/environment identities, timestamps, and aggregate safety result | No new authority can be promoted |
| Owner authorization | `ABSENT` | Written approval names the exact path, source commit, inputs, commands, resources, and gate packet | Every mutating or confirmatory action remains prohibited |

## Mechanical path compatibility

The audits may report compatibility, not authorization:

- **A-compatible:** every duplicated row proposed for removal is physically confirmed and exact; any merge-only defect is separately rebuilt; canonical inventory has no quarantine.
- **B-compatible:** nonidentical defects are bounded to a coherent affected component/batch and authoritative retained source material exists for that entire scope.
- **C1 file-availability-compatible:** every selected proposed physical source has an extracted NPZ file and no quarantine remains. This is not C1 authorization until cohort-wide readability/schema/content validation passes.
- **C2-compatible:** C1 fails only because extracted clips are missing/invalid, while retained authoritative DICOM coverage is complete.
- **C3-compatible:** required DICOMs are absent, source mapping cannot be validated, or a clean selected-only restart is required.

If more than one condition applies, the project must choose one coherent provenance strategy rather than silently mixing historical and new vectors.

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

Per-group identifiers, locators, hashes, frame metadata, vector hashes/norms, extraction metadata, and proposed source rows remain restricted. Only the eight aggregate files may be pasted back after both safety gates pass.

## Decision meeting inputs after SCC

Before requesting authorization, present:

- the eight safe aggregate files;
- a restricted reviewer attestation that all per-group classifications follow the prespecified precedence and no vector-only deduplication occurred;
- the availability-compatible A/B/C1/C2/C3 paths;
- path-specific storage, CPU/GPU, queue/I/O, and transfer estimates using observed counts; and
- the proposed source commit, exact commands, checkpoint, environment-capture plan, and preservation manifest schema.

Until that review and written owner decision, the status remains **NO-GO**.
