# Embedding authority decision

## Decision status

**No deduplication, re-extraction, re-embedding, pooling, download, or confirmatory use is authorized.** The Phase 1D audits are model-independent decision inputs. They do not mutate the historical stores and cannot promote an embedding authority by themselves.

The historical merged clip and study stores remain blocked because 32 selected-cohort clip keys are duplicated and the historical checkpoint/environment linkage is incomplete. Phase 1D separates physical-source evidence availability, duplicate classification, proposed resolution, and selected-cohort clip availability. Vector equality is corroborating evidence only and never independently permits deduplication.

## Nonnegotiable authority contract

Any future confirmatory vision input must:

- contain only the imaging-eligible studies from the selected one-study-per-subject cohort and exclude all 171 prior/nonselected Stage-D studies;
- contain one canonical row per adjudicated physical source clip, with every excluded, deduplicated, or quarantined row accounted for;
- preserve selected subject-study ownership and deterministic splits without exporting identifiers to Git;
- use 512-dimensional encoder-only clip vectors and one finite float32 mean-pooled 512-dimensional vector per imaging-eligible study;
- use checkpoint SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b` unless a different official checkpoint is independently verified and frozen before execution;
- capture source commit, exact commands, config, checkpoint, Python, PyTorch, scikit-learn, CUDA/cuDNN, GPU, scheduler/job, timestamps, and input/output checksums;
- issue fresh clip-, study-, environment-, command-, and preservation manifests using safe relative paths; and
- avoid outcomes, labels, predictions, and confirmatory performance throughout construction and quality control.

## Five candidate paths

| Path | Prerequisites and duplicate handling | Source availability and denominator | Compute, storage, and queue/I/O | Checkpoint, environment, manifests, and checksums | Advantages, limits, and manuscript-grade provenance |
|---|---|---|---|---|---|
| **A. Deterministic deduplication and repooling of existing component vectors** | Every affected group must be `EXACT_REPEATED_MANIFEST_ROW_CONFIRMED`, or a separately proven `MERGE_OR_INDEX_REWRITE_ONLY` defect. Physical-source hashes, ownership, component/merged correspondence, stored/recomputed L2 checks, and the selected-only canonical inventory must pass. Retain one prespecified component row; never select by vector value or downstream performance. | Existing component vectors survive and exactly align to manifests. Build only the common imaging-eligible selected denominator; exclude 171 nonselected studies and every unresolved/quarantined group. | No GPU. Stream about 0.4 GB of float32 clip vectors; reserve roughly 2–5 GB for rebuilt arrays, manifests, checksums, and audit scratch. Usually under one hour after adjudication, excluding queue and review. | Historical checkpoint/environment use remains unproven. Fresh source/command/output checksums and pooling manifests are still required. | Fastest and least resource intensive. Manuscript-grade only as a conditionally reconstructed historical representation with an explicit historical-environment limitation; it is not an exact historical-reproduction claim. Any vector-only or locator-only inference disqualifies A. |
| **B. Affected-batch re-extraction and re-embedding** | Use when an affected group is a same-source vector disagreement, physical/key collision, or component/merge defect whose scope is bounded. Repair the key/source mapping, quarantine ambiguity, re-extract when needed, and re-embed a coherent affected scope. | Requires retained authoritative source DICOMs or validated extracted clips for the whole affected scope. The final denominator remains selected and imaging eligible. A batch-wide fault means the batch, not only 32 rows, is the minimum repair unit. | For `batch_000` scale, historical throughput suggests under one GPU-hour for encoding, but allow roughly 2–8 hours for validation and I/O. Retained DICOM/extracted staging may require tens to about 130 GB. | Pin one checkpoint/environment for every regenerated vector and issue fresh affected-input, clip, merge, study, and preservation manifests. Mixing newly generated vectors with unverifiable historical vectors requires an explicit comparability justification and sensitivity boundary. | Repairs a bounded defect without a full restart. Provenance is weaker than C1–C3 because it can mix environments; manuscript-grade only if affected scope is proven complete and the mixed-store limitation is accepted prospectively. |
| **C1. Full selected-only re-embedding from surviving canonical extracted clips** | The canonical inventory must first show file-availability compatibility for every proposed selected physical source, zero unresolved/quarantined groups, exact 4,525-study coverage, and a deterministic one-row-per-source proposal. A separate cohort-wide NPZ readability, required-array/schema, and content-hash validation must then pass before C1 can be authorized. Re-embed every selected canonical clip, not only duplicates. | No source DICOM access is required for encoding after extracted authority is validated, although source mapping must remain traceable. Denominator is the 4,525 imaging-eligible selected studies; the five no-cine studies remain excluded from every primary modality. | Historical estimates imply up to roughly 288 GB if approximately 192k extracted clips average 1.5 MB. Pure encoder extrapolation is about 2.2 hours; request 4–8 GPU wall hours and plan 0.5–2 days including checksum I/O, validation, and queue. | Use one pinned checkpoint and fully captured new environment. Create fresh selected-only clip and study arrays, ordered manifests, input/content/output checksums, and a complete post-run preservation manifest. | Preferred when all extracted clips survive and subsequently validate. Strong prospective, manuscript-grade provenance with uniform representation generation and no inherited duplicate weighting. It does not reconstruct the historical environment; it creates a new revalidation authority. |
| **C2. Full selected-only re-extraction from retained source DICOMs, then re-embedding** | Use when extracted NPZs are missing or invalid but every proposed physical source has an authoritative retained DICOM locator/file. Resolve all collisions first, then re-extract and re-embed the complete selected canonical source set under one specification. | Requires complete retained DICOM coverage for all imaging-eligible selected physical sources; no redownload. Denominator and five-study rule are identical to C1. | CPU/I/O and temporary storage exceed C1. Historical planning used roughly 130 GB raw staging per 500-study batch and up to about 288 GB for extracted clips; GPU request remains about 4–8 hours after extraction. Plan staged jobs and verify quota first. | Pin extraction parameters, checkpoint, and full software/hardware environment. Hash DICOM inputs, extracted arrays, clip vectors, pooled study vectors, commands, and manifests. | Stronger source-to-vector lineage than C1 when DICOMs are retained, at higher I/O cost. Manuscript-grade if source completeness, extraction determinism, and all preservation checks pass. |
| **C3. Selected-only redownload and clean extraction/embedding restart** | Use only when required source DICOMs are absent or the retained/extracted authority cannot be validated. Reconstruct the selected imaging-eligible source set from the public release, then run clean audit, extraction, embedding, pooling, and preservation stages. | Requires authorized data access and redownload for all missing sources; a full selected-only restart is favored when missingness is broad or mapping trust is lost. Denominator remains selected and imaging eligible; repeated/all-study expansion is outside this path. | Historical planning estimated roughly 1.2 TB total raw transfer for 4,525 studies with about 130 GB staged per 500 studies, plus extracted storage. End-to-end planning range was 24–36 hours plus scheduler, transfer, and validation time; verify current SCC quota and network policy. | Strongest end-to-end prospective capture: release identity, download manifest, source hashes, extraction environment, pinned checkpoint, clip/study manifests, and second-pass preservation verification. | Highest cost and operational risk, but clearest manuscript-grade provenance when prior source authority is inadequate. Requires separate explicit authorization for download and processing. |

## Decision rule after the aggregate-only audits

1. Consider **A** only when physical/file evidence—not vector equality—confirms every duplicate eligible for deduplication and the canonical inventory otherwise passes.
2. Consider **B** when defects are nonidentical but demonstrably bounded to an affected batch or coherent processing unit and retained sources are sufficient.
3. Prefer **C1** when every selected canonical extracted clip is file-available and a separate cohort-wide readability/schema/content validation passes.
4. Use **C2** when extracted clips are incomplete but retained source DICOM coverage is complete.
5. Use **C3** when required source DICOMs are absent, mapping authority fails, or a clean restart is otherwise necessary.
6. If different conditions occur in different components, choose the highest-provenance coherent path that avoids mixing unverifiable historical and new vectors. Do not assemble a hybrid merely because it is faster.

The aggregate inventory may identify availability-compatible paths, but the owner must select and authorize one after resource review. Path choice may not use model performance.

## Required evidence before any path can be promoted

The selected path must eventually demonstrate:

1. exact selected-cohort containment and zero outside-selected studies;
2. one canonical row per physical source and explicit counts for deduplicated, excluded, and quarantined rows;
3. zero unresolved ownership, source-locator, or key-collision failures;
4. exact manifest/array row-index correspondence and finite float32 width-512 vectors;
5. one mean-pooled study vector per imaging-eligible selected study and no others;
6. deterministic canary equality under the chosen new pipeline where regeneration occurs;
7. complete source, command, environment, checkpoint, clip, study, and preservation manifests with second-pass checksums; and
8. a passing aggregate export safety gate with no identifier-level Git output.

Embedding authority alone does not open confirmatory test access. Clinical, denominator, statistical, config-checksum, and explicit owner-authorization gates remain separate.
