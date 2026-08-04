# Embedding authority decision

## Decision status

**No historical-store repair, full selected-cohort re-extraction/re-embedding, or confirmatory use is authorized.** The Phase 1D audits are model-independent decision inputs. They do not mutate the historical stores and cannot promote an embedding authority by themselves. A later Phase 1E-A owner authorization permitted one bounded four-training-study technical smoke; that exception did not authorize C3 or alter this historical-store ruling.

The historical merged clip and study stores remain blocked. Restricted adjudication classified all 32 selected-cohort duplicate groups as `SOURCE_ARTIFACT_PURGED`: their vectors, L2 values, normalized manifest/extraction metadata, and component/merged correspondence are concordant, but the source DICOM and extracted NPZ content/hashes needed to establish physical-source identity are unavailable. Zero residual normalized tuple disagreement therefore removes numeric serialization and expected index rewriting as the scientific blocker; it does not prove that any group is an exact repeated physical clip. Vector equality is corroborating evidence only and never independently permits deduplication.

The selected-source inventory also rules out a retained-artifact rebuild. Only the 14,006 Stage D physical sources retain extracted NPZs. The 170,568 `batch_000`–`batch_008` physical sources retain neither NPZ nor DICOM. Accordingly, A is prohibited, C1 and C2 are unavailable, and B is not available as a provenance-complete retained-source repair. C3 is the only operationally clean prospective path, but remains unauthorized.

## Phase 1D observed authority evidence

| Evidence item | Aggregate finding | Authority consequence |
|---|---:|---|
| Selected studies seen in embedding manifests | 4,525 | Historical imaging coverage before quarantine. |
| Selected studies with at least one proposed nonquarantined canonical clip | 4,524 | The confirmatory imaging denominator is not yet restored; one selected study loses proposed coverage under quarantine. |
| Selected studies without embedded clips | 5 | Prespecified imaging-ineligible studies with readable DICOM but no multiframe cine candidate; excluded from all modalities in primary paired comparisons. |
| Selected embedded manifest rows | 184,606 | Historical selected rows before physical-source grouping. |
| Outside-selected rows excluded | 7,387 | Cannot enter the one-study-per-subject authority. |
| Selected physical-source groups | 184,574 | Canonical physical-source grouping denominator before quarantine resolution. |
| Surviving extracted NPZ physical sources | 14,006 | All are Stage D; survival alone is not readability, schema, content, or source-mapping authority. |
| Physical sources with neither NPZ nor retained DICOM | 170,568 | `batch_000`–`batch_008` require authorized DICOM redownload and re-extraction. |
| Quarantined physical-source groups | 32 | All are `SOURCE_ARTIFACT_PURGED`; deterministic deduplication is prohibited. |

These counts are Git-safe aggregate findings. Identifier-level adjudications, locators, hashes, and proposed source rows remain restricted.

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

The matrix below records each path's prespecified prerequisites and resource envelope. The Phase 1D disposition following the matrix is controlling.

| Path | Prerequisites and duplicate handling | Source availability and denominator | Compute, storage, and queue/I/O | Checkpoint, environment, manifests, and checksums | Advantages, limits, and manuscript-grade provenance |
|---|---|---|---|---|---|
| **A. Deterministic deduplication and repooling of existing component vectors** | Every affected group must be `EXACT_REPEATED_MANIFEST_ROW_CONFIRMED`, or a separately proven `MERGE_OR_INDEX_REWRITE_ONLY` defect. Physical-source hashes, ownership, component/merged correspondence, stored/recomputed L2 checks, and the selected-only canonical inventory must pass. Retain one prespecified component row; never select by vector value or downstream performance. | Existing component vectors survive and exactly align to manifests. Build only the common imaging-eligible selected denominator; exclude 171 nonselected studies and every unresolved/quarantined group. | No GPU. Stream about 0.4 GB of float32 clip vectors; reserve roughly 2–5 GB for rebuilt arrays, manifests, checksums, and audit scratch. Usually under one hour after adjudication, excluding queue and review. | Historical checkpoint/environment use remains unproven. Fresh source/command/output checksums and pooling manifests are still required. | Fastest and least resource intensive. Manuscript-grade only as a conditionally reconstructed historical representation with an explicit historical-environment limitation; it is not an exact historical-reproduction claim. Any vector-only or locator-only inference disqualifies A. |
| **B. Affected-batch re-extraction and re-embedding** | Use when an affected group is a same-source vector disagreement, physical/key collision, or component/merge defect whose scope is bounded. Repair the key/source mapping, quarantine ambiguity, re-extract when needed, and re-embed a coherent affected scope. | Requires retained authoritative source DICOMs or validated extracted clips for the whole affected scope. The final denominator remains selected and imaging eligible. A batch-wide fault means the batch, not only 32 rows, is the minimum repair unit. | For `batch_000` scale, historical throughput suggests under one GPU-hour for encoding, but allow roughly 2–8 hours for validation and I/O. Retained DICOM/extracted staging may require tens to about 130 GB. | Pin one checkpoint/environment for every regenerated vector and issue fresh affected-input, clip, merge, study, and preservation manifests. Mixing newly generated vectors with unverifiable historical vectors requires an explicit comparability justification and sensitivity boundary. | Repairs a bounded defect without a full restart. Provenance is weaker than C1–C3 because it can mix environments; manuscript-grade only if affected scope is proven complete and the mixed-store limitation is accepted prospectively. |
| **C1. Full selected-only re-embedding from surviving canonical extracted clips** | The canonical inventory must first show file-availability compatibility for every proposed selected physical source, zero unresolved/quarantined groups, exact 4,525-study coverage, and a deterministic one-row-per-source proposal. A separate cohort-wide NPZ readability, required-array/schema, and content-hash validation must then pass before C1 can be authorized. Re-embed every selected canonical clip, not only duplicates. | No source DICOM access is required for encoding after extracted authority is validated, although source mapping must remain traceable. Denominator is the 4,525 imaging-eligible selected studies; the five no-cine studies remain excluded from every primary modality. | Historical estimates imply up to roughly 288 GB if approximately 192k extracted clips average 1.5 MB. Pure encoder extrapolation is about 2.2 hours; request 4–8 GPU wall hours and plan 0.5–2 days including checksum I/O, validation, and queue. | Use one pinned checkpoint and fully captured new environment. Create fresh selected-only clip and study arrays, ordered manifests, input/content/output checksums, and a complete post-run preservation manifest. | Preferred when all extracted clips survive and subsequently validate. Strong prospective, manuscript-grade provenance with uniform representation generation and no inherited duplicate weighting. It does not reconstruct the historical environment; it creates a new revalidation authority. |
| **C2. Full selected-only re-extraction from retained source DICOMs, then re-embedding** | Use when extracted NPZs are missing or invalid but every proposed physical source has an authoritative retained DICOM locator/file. Resolve all collisions first, then re-extract and re-embed the complete selected canonical source set under one specification. | Requires complete retained DICOM coverage for all imaging-eligible selected physical sources; no redownload. Denominator and five-study rule are identical to C1. | CPU/I/O and temporary storage exceed C1. Historical planning used roughly 130 GB raw staging per 500-study batch and up to about 288 GB for extracted clips; GPU request remains about 4–8 hours after extraction. Plan staged jobs and verify quota first. | Pin extraction parameters, checkpoint, and full software/hardware environment. Hash DICOM inputs, extracted arrays, clip vectors, pooled study vectors, commands, and manifests. | Stronger source-to-vector lineage than C1 when DICOMs are retained, at higher I/O cost. Manuscript-grade if source completeness, extraction determinism, and all preservation checks pass. |
| **C3. Selected-only redownload and clean extraction/embedding restart** | Use when required source DICOMs are absent or the retained/extracted authority cannot be validated. Under the observed inventory, validate the surviving Stage D NPZs and source mapping, redownload and re-extract the selected `batch_000`–`batch_008` sources, then re-embed the complete selected canonical clip set under one specification. If Stage D validation fails, redownload and re-extract that scope too. | Requires authorized data access and redownload for all missing selected sources. Denominator remains selected and imaging eligible; outside-selected rows and repeated/all-study expansion are outside this path. | Historical planning estimated roughly 1.2 TB total raw transfer for a full 4,525-study restart with about 130 GB staged per 500 studies, plus extracted storage. Reuse of validated Stage D NPZs may reduce transfer, but an observed-path estimate must be produced before authorization. End-to-end planning range was 24–36 hours plus scheduler, transfer, and validation time; verify current SCC quota and network policy. | Strongest end-to-end prospective capture: release identity, download manifest, source hashes, extraction environment, pinned checkpoint, clip/study manifests, and second-pass preservation verification. | Highest cost and operational risk, but clearest manuscript-grade provenance when prior source authority is inadequate. Requires separate explicit authorization for download and processing. |

## Phase 1D path disposition

| Path | Disposition | Basis |
|---|---|---|
| **A** | `PROHIBITED` | No duplicate group has physical/extracted-content confirmation; all 32 are `SOURCE_ARTIFACT_PURGED`. |
| **B** | `UNAVAILABLE_AS_RETAINED-SOURCE_REPAIR` | Affected `batch_000` source artifacts are purged. Redownloading only the affected scope would create a mixed prospective/historical store without resolving the broad historical environment and source-authority limitation. |
| **C1** | `UNAVAILABLE` | Only 14,006 Stage D physical sources retain NPZs; 170,568 physical sources lack NPZs and 32 groups remain quarantined. |
| **C2** | `UNAVAILABLE` | Retained DICOMs are absent for the 170,568 `batch_000`–`batch_008` sources. |
| **C3** | `OPERATIONALLY_CLEAN_UNAUTHORIZED` | It can produce a uniform selected-only representation authority from validated Stage D inputs plus redownloaded/re-extracted batch inputs, with a pinned checkpoint and fully captured environment. |

Only C3 remains technically coherent for a manuscript-grade prospective authority. This is a recommendation for an authorization decision, not authorization itself. It may not begin until the owner approves its exact selected-source scope, resource plan, commands, checkpoint/environment contract, and preservation schema. Path selection and subsequent quality control may not use outcomes or model performance.

## Phase 1E-A technical-smoke evidence

The four-study prospective smoke passed at commit `022d7581eee4cd0278b29c9213e4b65bdc6161b2`. It verified exact-object transport and DICOM-header readability for 252 public objects, successfully pixel-decoded and extracted 123 multiframe candidates across the three positive controls and zero in the no-multiframe negative control, reproduced 123 encoder-only width-512 clip embeddings and three mean-pooled study vectors exactly across two clean runs, and passed the aggregate safety and preservation second-pass gates. Exact new-run checkpoint file identity and strict encoder loading were established, but official-release provenance and historical checkpoint use remain unproven. Full findings are recorded in [the Phase 1E-A smoke report](phase1e_reconstruction_smoke_findings.md).

This establishes bounded operational feasibility for the proposed source-to-vector chain. It does not validate the full 4,530-study source set, decide the final cine/preprocessing specification, establish a canonical selected clip inventory, prove historical checkpoint/environment use, or authorize the full C3 path. The historical 32-group quarantine and 4,524/4,525 non-authority remain unchanged.

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
