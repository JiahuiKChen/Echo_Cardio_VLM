# Phase 1B SCC follow-up findings

## Authority and safety boundary

This record interprets the complete aggregate-only SCC follow-up run supplied on 2026-08-03. The run used the dedicated SCC worktree and the committed Phase 1B follow-up commands. Its final safety gate reported seven CSV files and four JSON summaries checked, zero issues, and `safety_gate_passed = true`. No identifier-, label-, prediction-, path-, clip-, or embedding-level output is reproduced here.

The follow-up narrows provenance questions; it does not authorize model fitting, prediction regeneration, confirmatory performance access, modification of historical artifacts, or use of a restricted artifact as authority merely because it exists.

## Decision summary

| Follow-up | What it proves | What it does not prove | Current effect |
|---|---|---|---|
| Preservation manifest | The old pack has 14 manifest lines; all 14 fail the safe-relative-path rule. Thirteen allowlisted files are therefore unlisted. No valid entry produced a missing-file or byte-mismatch event. | It does not checksum-validate any expected file: path validation failed before expected-file hashing. `byte_mismatch = 0` is not evidence of byte equality. It does not repair or validate the pack. | Old pack is immutable, not fully checksum-validated, and unsuitable as future revalidation preservation authority. This blocks a historical-pack checksum claim, not a new run backed by fresh independent authorities. |
| Clip-component union | Component and merged stores agree in row count, key set, key multiplicity, ownership, and component membership. The original 9,605 full-row mismatches have zero residual full-row tuple mismatches after the declared reconciliation. Expected `embedding_idx` rewrites and numeric serialization concentrated in `embedding_l2_norm` explain those 9,605 records. Exactly 32 duplicated keys remain, all in selected-cohort `batch_000`, in both component and merged manifests. | It does not establish whether each duplicated key is an exact row repeat, the same clip embedded twice, a collision between different clips, or a merge defect. Manifest comparison alone does not prove clip-content or embedding-vector identity. | The 9,605 finding is closed as non-scientific serialization/index transformation. The 32 duplicate keys remain a blocker to historical merged/study embedding authority, vision/fusion confirmatory input, and any “one canonical row per physical clip” claim. |
| Five studies without embeddings | Exactly five selected studies stop after readable DICOM and before multiframe cine candidacy. All five have at least one `legacy29` label; three have numeric exact-raw LVEF before imaging linkage. The split distribution is three train, one validation, and one test. | It does not independently reread source DICOM pixels or prove that no alternative nonhistorical imaging-usability definition would accept them. It does not provide an imaging representation for those studies. | Provisionally classify the five as imaging-ineligible under the historical multiframe-cine rule. Exclude them from every modality in primary paired comparisons; a structured-only full-availability sensitivity is unpaired and separately labeled. |
| Checkpoint and environment | The current file is `echo_prime_encoder.pt`, 138,642,379 bytes, SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`. Both expected historical metadata files exist and their identities were recorded. | Historical metadata contains no exact checkpoint-hash linkage and does not establish Python, package, PyTorch, CUDA/cuDNN, or source-commit provenance for historical embedding generation. | Current-file identity is known; historical-use identity is not. This limits historical reproducibility and blocks reuse of historical embeddings as a fully provenance-locked confirmatory authority. A new authorized run must capture its own environment. |
| Aggregate safety | The supplied follow-up packet passed its fixed-schema, fixed-vocabulary aggregate safety gate. | It does not make the restricted inputs or restricted diagnostic stderr safe to copy to Git. | Aggregate findings may be documented. Restricted rows, paths, keys, hashes tied to clips, and stderr stay on SCC. |

## Preservation-manifest defect

The exact aggregate counts are:

| Status | Count |
|---|---:|
| manifest lines | 14 |
| invalid relative path | 14 |
| unlisted file | 13 |
| malformed line | 0 |
| duplicate entry | 0 |
| missing file | 0 |
| byte mismatch | 0 |

Every manifest entry was rejected at path validation. Consequently, the follow-up found no expected-file byte mismatch only in the literal sense that no expected-file entry reached a valid checksum comparison. It would be incorrect to translate the zero into “all expected bytes match.” The old `SHA256SUMS.txt` remains immutable.

This defect does not alter the separate historical role of the immutable Git snapshot under `docs/results_snapshot/2026-04-01_fullscale/`. It does prevent the SCC preservation pack from serving as a future revalidation authority.

## Reinterpretation of the 9,605 clip-manifest mismatches

Phase 1A correctly treated 9,605 non-index payload mismatches as unresolved because only an aggregate row-payload comparison was then available. Phase 1B reproduced all 9,605 original mismatch keys and decomposed them by field and expected transform:

- every full-row tuple residual mismatch count after reconciliation is zero;
- the numeric-serialization review is concentrated in `embedding_l2_norm`;
- merged `embedding_idx` changes are expected because merging deliberately rewrites indices;
- component and merged key sets, multiplicities, study/subject ownership, and successful row counts remain identical.

Therefore the 9,605 mismatches are not evidence of different clips, different embedding vectors, or altered study ownership. Numeric serialization and index rewriting are no longer scientific blockers. This conclusion does not dispose of the independent 32-key duplication defect.

## The remaining 32-key blocker

The follow-up establishes 32 unique duplicated keys, all in `batch_000`, all in the selected cohort, and present with the same multiplicity in the component union and merged manifest. The restricted diagnostic in `scripts/audit_duplicate_clip_keys.py` must next compare manifest rows, ownership, locators, available file hashes, extracted-frame shape/content, paired component and merged vectors, `embedding_l2_norm`, and `write_ok`.

The diagnostic uses these mutually exclusive dispositions:

1. `EXACT_REPEATED_MANIFEST_ROW`;
2. `SAME_CLIP_EMBEDDED_TWICE`;
3. `DIFFERENT_CLIPS_SHARE_NONUNIQUE_KEY`;
4. `MERGE_REINDEXING_DEFECT`;
5. `OTHER_UNRESOLVED`.

Only grouped counts by disposition, safe component label, selected/outside-selected scope, and proposed resolution may leave SCC. The audit itself does not deduplicate or regenerate anything. Even an exact-duplicate classification remains blocking until a clean canonical store is built or the project explicitly selects another embedding-authority path.

## Imaging eligibility and denominator rule

For the primary estimand, imaging eligibility means at least one validated canonical multiframe cine clip that can enter the locked vision pipeline. Under the historical candidate rule, the five studies have no such clip and are provisionally imaging-ineligible rather than unexplained downstream failures. They are not failures of extraction, embedding, or study aggregation because they never enter those stages.

The prespecified denominator consequences are:

- primary vision-only, structured-only, and early-fusion comparisons use the identical imaging-eligible subject-study set;
- the five imaging-ineligible studies are excluded from all three primary modality inputs before any preprocessing;
- target-specific observed-label filtering occurs only after that common imaging-eligibility intersection;
- a structured-only full-availability sensitivity may retain imaging-ineligible studies, but its denominator must be displayed separately and it cannot support paired modality or incremental-value claims;
- the accepted abstract remains an immutable historical report and is not silently recomputed or rewritten.

## Claim and access boundaries

| Issue | Embedding authority | Primary modality comparison | Manuscript claim | Confirmatory test access |
|---|---|---|---|---|
| Invalid old freeze manifest | Old pack cannot be authority | Does not preclude a newly preserved run | Prohibits “historical pack checksum-validated” | Does not alone block if every future input has independent authority |
| Resolved 9,605 serialization/index differences | No remaining adverse effect | No adverse effect | May be described as reconciled manifest serialization/index transforms | Not a blocker |
| Unresolved 32 duplicate keys | Blocks historical merged/study store | Blocks use of those historical embeddings | Prohibits canonical-clip/fully validated embedding lineage claims | Blocking |
| Five imaging-ineligible studies | No embedding can be expected | Requires common exclusion in primary comparison | Requires explicit attrition and sensitivity denominator | Rule is provisionally resolved; exact common-denominator gate still must pass |
| Historical checkpoint/environment gap | Blocks exact historical-generation claim | New-run inputs can cure prospectively | Requires limitation if unrecoverable | Blocking for reuse of historical store as fully locked authority; not for an authorized new pinned run |

## Remaining restricted work

1. Run the 32-key audit and retain both restricted detail tables on SCC.
2. If extracted clip hashes are unavailable for any duplicated group, determine whether the clip cache was intentionally purged and whether affected DICOMs must be re-extracted.
3. Inventory all selected-cohort extracted clips, not only the 32 duplicate groups, before choosing a selected-only re-embedding path.
4. After a clean embedding path is explicitly authorized, build fresh clip/study manifests and a post-revalidation preservation manifest. Do not modify the old pack.

No confirmatory model access is opened by this document.
