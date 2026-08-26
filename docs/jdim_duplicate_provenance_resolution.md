# JDIM Duplicate Provenance Resolution

Status: `DUPLICATE_SEMANTICS_RESOLVED_FROM_PROVENANCE`

This note records the metadata-only resolution of the 32 repeated historical
clip-key groups found during the JDIM-D-26-02840 revision. Classification used
input lineage and frozen-vector identity only. It did not use target values,
predictions, residuals, or performance direction.

## Semantic embedding unit

The historical full-scale pathway has no temporal-window or segment concept.

1. `extract_mimic_echo_cines.py::pick_rows` filters readable multiframe audit
   rows and sorts them without deduplication.
2. `extract_one` derives one output NPZ path deterministically from one relative
   DICOM path, temporally samples one 32-frame sequence, and writes one `frames`
   array plus one `sampled_indices` array.
3. `extract_echoprime_embeddings.py::load_clip_tensor` opens that one `frames`
   array. `prepare_clip` truncates or pads it to 32 frames and takes stride 2,
   yielding one 16-frame encoder input.
4. `_run_inference` appends one 512-dimensional vector for each successful
   extraction-manifest row. `embedding_idx` is `len(embeddings)` at append time;
   it is a row position, not a clip/window identifier.
5. `merge_batch_embeddings.py` concatenates arrays and manifests and reassigns
   `embedding_idx` as the running merged row position. It does not deduplicate.
6. `aggregate_study_embeddings.py` mean-pools every retained clip row, so an
   unintended repeated row receives repeated weight.

The semantic clip key for this historical pipeline is therefore the stable
source DICOM path together with its deterministic processed NPZ path and pinned
preprocessing version. There is no supported second temporal-window component.

## Git and execution lineage

The full-scale runner was introduced at
`f6187888027a3321ec76201f8403f81ee0bc22e6`. The extraction, embedding,
merge, and aggregation scripts are byte-identical between that commit and the
required Phase 2D base `6d21a836ada8243d9a6dc1f47d22823e2575e5b5`.

The runner:

- creates each batch records CSV directly from one `echo_record_list` query;
- passes the records CSV through audit, extraction, and embedding without a
  deduplication step;
- skips a whole batch when its final embedding NPZ already exists;
- writes checkpoint files under distinct names but has no checkpoint-resume
  loader that appends prior successful rows;
- supplies each completed batch directory once to a pure concatenation merge;
- purges source DICOMs and processed NPZs after successful embedding.

Historical aggregate summaries report 20,614 requested and successful
extraction rows for `batch_000`, 20,614 successful 512-dimensional embedding
rows, and 191,993 rows after the ten-source merge. These counts are consistent
with row-for-row propagation and do not independently identify the duplicate
origin.

## Metadata-only SCC result

The bounded inspector streamed only six explicitly named CSV manifests and the
64-row prior restricted evidence file. It did not open DICOM pixels, processed
NPZ archives, or embedding arrays.

- affected groups: 32
- affected prior rows: 64
- matched manifest rows: 384, exactly two rows per group at each of six stages
- groups first repeated in expected records: 32
- groups first repeated at later stages: 0
- groups with equal semantic identity fields: 32
- groups with positive distinct clip/window metadata: 0
- groups with exact historical vector identity: 32
- groups requiring reconstruction: 0
- classification: 32 `TRUE_DUPLICATE_EXPECTED_ROWS`

The aggregate-safe metadata summary records these restricted evidence hashes:

| Restricted evidence role | SHA-256 |
| --- | --- |
| input provenance | `54e89ffd1660f53d60cdf0d6fbacbe7f97b9a6b1db10fef7ed52a6df39d5c744` |
| field equality | `e51aa3d7e475825f282db3cce60ff3994a9587541c37d89dc9079618b825a136` |
| group classification | `30dff35f0a63c825500e349242d5c1d719fc1b101c68e945abb38adc12d95a9b` |
| matched stage rows | `773899ec71a5d26a8ac77a0da5754727615b8446bdc2ed2d1a9d2ae4b1660a06` |
| recovery readiness | `ff4b772cb6a79958ce191c999a39e9f0b6cbfed886c7ac76cf421177c224c132` |

The recorded processed NPZs and exact declared source-DICOM candidates are no
longer available, as expected after the historical purge. That absence does not
block classification because multiplicity is already present in the expected
records and every downstream row retains one source path, one processed path,
no finer window identity, and exact vector identity.

## Interpretation proof matrix

| Interpretation | Evidence for | Evidence against | Missing evidence | Status |
| --- | --- | --- | --- | --- |
| Legitimate distinct clips | None | No group has a distinct source, window, segment, frame-selection, or processed-path identity | Positive finer identity would be required | `CONTRADICTED` |
| Duplicate expected-list rows | All 32 groups first repeat in expected records and propagate two-for-two | None | None | `SUPPORTED` |
| Duplicate extraction rows | Extraction preserves the two rows | Multiplicity already exists before extraction | None | `CONTRADICTED` |
| Duplicate embedding rows | Embedding preserves two exact vectors at different row positions | Multiplicity already exists before embedding | None | `CONTRADICTED` |
| Resume/concatenation duplication | None | Batch rows are already duplicated before merge; checkpoints are not resumed | None | `CONTRADICTED` |
| Coarse key only | None | The pipeline stores no finer clip/window field and both rows use one source and processed path | Positive finer identity would be required | `CONTRADICTED` |

## Scientific consequence

One representative row must be retained from each pair under a deterministic,
target-independent rule. Because all pairs belong to one training study that is
present in both target cohorts, the canonical study embedding must be
reaggregated from unique rows. The unchanged stable-v2 protocol must then be
rerun for both targets, and all reported numbers must be replaced from the
corrected outputs regardless of performance direction. Cohort flow and input
audit sampling remain downstream of that correction.

