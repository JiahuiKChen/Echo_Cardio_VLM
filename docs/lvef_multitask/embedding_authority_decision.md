# Embedding authority decision

## Decision status

**No embedding regeneration is authorized.** The historical merged clip and study stores remain blocked as confirmatory authorities because 32 selected-cohort clip keys are duplicated and the inspected historical metadata does not establish checkpoint/environment linkage.

The preferred future authority is **Path C: a clean selected-cohort-only EchoPrime embedding store**, conditional on validating the canonical extracted cine clips and their source mapping. Path A is acceptable only if the restricted audit proves all 32 groups are exact/content-identical duplicates and governance explicitly accepts reuse of the otherwise historical component vectors. Path B is required if any duplicate is nonidentical, key-colliding, or affected by a component-level defect. The owner must authorize the chosen path after the restricted audit; none may be selected from downstream model performance.

## Nonnegotiable authority contract

Any future confirmatory vision input must:

- contain only the 4,525 imaging-eligible selected-cohort studies, subject to a fresh exact audit;
- exclude all 171 Stage-D studies outside the selected one-study-per-subject cohort;
- contain exactly one canonical row for each physical extracted cine clip;
- preserve the selected study-to-subject ownership mapping and deterministic split map without exposing identifiers to Git;
- use 512-dimensional encoder-only clip vectors and one float32 mean-pooled 512-dimensional study vector per imaging-eligible study;
- use checkpoint SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b` unless an independently verified official checkpoint is explicitly chosen and documented before the run;
- capture command, config, source commit, checkpoint, Python, PyTorch, scikit-learn, CUDA/cuDNN, scheduler/job, device, timestamp, and input/output checksums;
- create fresh clip- and study-level manifests and pass shape, index, finite-value, uniqueness, ownership, selected-containment, and aggregate-safety gates;
- avoid outcomes, labels, predictions, and confirmatory performance throughout embedding construction and QC.

Raw DICOM re-download or re-extraction is reserved for clips whose extracted authority is absent, unreadable, content-inconsistent, or inadequately mapped. A full DICOM restart is not the default and requires separate evidence and authorization.

## Path comparison

| Path | Work | Scientific strengths | Main risks | Resolution evidence required |
|---|---|---|---|---|
| A. Deterministically deduplicate and rebuild from existing clip vectors | Classify the 32 groups, retain one canonical row for each proven exact/content-identical physical clip, exclude nonselected studies, concatenate validated component vectors, then mean-pool anew | Fast; no GPU; removes duplicate contribution and nonselected studies while preserving historical representations | Does not recover historical checkpoint/environment provenance; unsafe if duplicate rows differ in clip content or vector; component stores still need complete checksums and ownership validation | All 32 groups `EXACT_REPEATED_MANIFEST_ROW` or `SAME_CLIP_EMBEDDED_TWICE`; extracted/content or other sufficient physical-identity evidence; exact component-vector/manifest alignment; owner accepts historical-environment limitation |
| B. Re-extract/re-embed affected clips or batch | Correct the key/source mapping and reprocess the affected physical clips; rebuild merge and study pool | Repairs nonidentical duplicates/collisions without necessarily repeating the whole cohort | Mixed old/new embedding environments can create a new comparability problem; targeted source DICOM or clip cache may be absent; a batch-wide defect may be larger than 32 keys | Classification as collision/nonidentity or batch defect; validated affected source mapping; one pinned environment/checkpoint for every regenerated clip; explicit policy on whether unaffected historical vectors may be mixed |
| C. Rebuild clean selected-only store from canonical extracted clips | Validate selected extracted cine authority, deduplicate by physical content/source mapping, re-embed every canonical selected clip with one pinned environment/checkpoint, then mean-pool | Strongest prospective provenance; removes 171 nonselected studies; uniform checkpoint/environment; simplest manuscript claim boundary | Extracted clips may have been purged by historical batch workflow; requires storage/GPU and a new authorized run; cannot proceed until selected clip authority is proven | Complete selected-cohort extraction manifest; accessible and checksum-valid clip files; zero unresolved physical duplicates/collisions; adequate SCC storage; pinned run manifest; owner authorization |

## Recommended path

Choose **Path C** if the selected-cohort extracted clips exist and pass source-to-extracted mapping and content-hash validation. It offers the cleanest authority: a uniform checkpoint and environment, a selected-only estimand, no inherited duplicate weighting, and a fully prospective preservation manifest.

If the historical batch-and-purge workflow removed most extracted cine files, do not silently convert Path C into a full DICOM restart. First determine whether a separate canonical clip cache exists. If not, report the storage/runtime implications and ask for specific authorization. Path A then remains a scientifically defensible fallback only if every duplicate is proven benign and all component embedding stores pass fresh independent checks.

Path B is a repair path, not the default. If any affected clip must be re-embedded, the project should prefer re-embedding a coherent unit with the same pinned environment rather than mixing unverifiable historical and new vectors without a written comparability justification.

## Resource estimates

These are planning ranges, not scheduler guarantees. The measured historical anchor is 21,393 Stage-D clips embedded in 867 seconds (14.5 minutes) on GPU. The historical merged manifest has 191,993 rows; the unique-key count is 191,961 before selected-only filtering. Historical planning estimated approximately 1.5 MB per extracted clip and about 130 GB of raw DICOM staging per 500-study batch.

| Path | GPU | CPU/I/O | Restricted storage | Practical wall-time range |
|---|---|---|---|---|
| A | None | Stream/read roughly 0.4 GB of float32 clip vectors, validate/deduplicate manifests, rewrite selected-only clip store, mean-pool studies, checksum outputs | Low; reserve 2–5 GB for old/new NPZs, manifests, checksums, and audit scratch | Usually under one hour after the 32-key audit, excluding human review |
| B, 32 affected clips only | Under one GPU-hour; pure encoder time is minutes | Hash/re-extract affected clips and rebuild merge/pool | 2–10 GB if source clips/DICOMs are already local | Several hours including validation and queue/I/O |
| B, all of `batch_000` | Pure encoder extrapolation for approximately 20,582 clips is about 15 minutes; request up to one GPU-hour plus queue/I/O | Batch source validation, possible extraction, full downstream rebuild | Embeddings are small; extracted/raw staging can require tens to roughly 130 GB | Roughly 2–8 hours depending on clip/DICOM availability |
| C, validated extracted clips reused | Pure GPU extrapolation is about 2.2 hours at the measured Stage-D throughput; practical request 4–8 GPU wall hours and no more than the standard 12-hour job until piloted | Hash/inventory up to roughly 192k clips, selected filtering, embedding I/O, pooling, independent verification | Approximately 288 GB if all extracted clips occupy 1.5 MB each, plus low-single-digit GB for embeddings/manifests and safety headroom | One or more staged jobs; roughly 0.5–2 days including validation/queue, not model fitting |
| C with DICOM restart | Not estimated from encoder time alone | Download and extract about 4,525 studies in batches; historical raw estimate is roughly 1.2 TB total with approximately 130 GB staged per 500 studies | Verify current SCC free space; retain only approved canonical derivatives and preservation artifacts | Historical full download→extract→embed planning range was 24–36 hours plus queue and validation; requires separate authorization |

Before Path B or C, capture `df`/quota information in restricted logs and run a small, outcome-blind throughput pilot. Neither storage inspection nor a pilot authorizes confirmatory modeling.

## Required aggregate evidence before promotion

The future embedding packet may be promoted only when aggregate checks show:

1. exact selected-cohort containment and zero outside-selected studies;
2. zero missing or duplicate canonical clip keys;
3. one subject per study and one selected study per subject;
4. clip-manifest row count equals the first dimension of the clip array;
5. all indices are unique, integral, in range, and map to the intended vector;
6. all vectors are finite float32 with dimension 512;
7. one mean-pooled study vector per imaging-eligible selected study and no others;
8. deterministic rerun equality for a prespecified canary subset without outcomes;
9. complete preservation and environment manifests with second-pass checksum validation;
10. aggregate export safety gate passes with zero identifier-level output.

Only after these checks, the denominator, clinical, statistical, config-checksum, and owner-authorization gates must still pass. Embedding authority alone does not open confirmatory access.
