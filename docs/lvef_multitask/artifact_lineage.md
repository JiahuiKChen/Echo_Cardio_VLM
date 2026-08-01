# Artifact lineage

## Authority classes

| Class | Example | Use |
|---|---|---|
| Accepted historical abstract | `accepted_abstract_version10_verbatim.txt` | Exact accepted language and reported values |
| Frozen historical aggregate snapshot | `docs/results_snapshot/2026-04-01_fullscale/` | Historical numeric evidence only |
| Historical implementation | scripts at base commit `23c74cc` | Reconstruct methods and identify gaps |
| Restricted SCC authority | split maps, manifests, mappings, predictions, environment metadata | Resolve lineage and run audit/revalidation |
| New aggregate revalidation output | future dated snapshot | Poster/manuscript analysis after SAP lock |
| Poster output | future ePoster source/export | Conference communication, labeled by analysis vintage |
| Manuscript output | future manuscript source/tables | Final scientific claims |

## Known pipeline lineage

1. Public MIMIC-IV-ECHO metadata contain 7,243 studies, 4,579 patients, and 525,422 DICOM record rows.
2. Historical fullscale selection required at least five DICOMs and structured-measurement linkage.
3. Read-only aggregate recomputation yielded 7,104 eligible studies among 4,530 patients.
4. A deterministic one-study-per-subject policy selected 4,530 studies.
5. A prior 500-study Stage-D selection was treated as already processed based on selection membership rather than verified embedding success.
6. Remaining DICOM batches were downloaded, audited, converted to multiframe cine arrays, embedded with the frozen EchoPrime video encoder, and purged after processing.
7. All successful clip embeddings were mean-pooled to one 512-dimensional study vector.
8. Prior Stage-D embeddings and new batch embeddings were merged.
9. The resulting store contains 191,993 clips, 4,696 study embeddings, and 4,525 subjects.
10. Structured measurements were exported for the selected 4,530-study cohort: 669,378 rows, 145,653 numeric-parsed rows, and 186 raw names.
11. LVEF labels were generated from exact raw name `lvef` and then inner-joined to an embedding-derived stub.
12. The linked LVEF cohort contains 2,833 studies; historical test n is 426.
13. The multitask registry reduced 186 raw names to 178 canonical tasks and a legacy known-unit/support panel of 29 tasks.

## Unresolved reconciliation

The scientific selected cohort contains 4,530 studies, but the embedding store contains 4,696 studies among only 4,525 subjects. Historical documentation indicates five selected studies apparently lack embeddings and additional prior-batch embeddings are present. The precise explanation must be reconstructed from restricted manifests rather than assumed.

Required SCC reconciliation:

- exact selected-versus-embedded study-ID set differences;
- subject ownership of additional embeddings;
- download/read/extraction/embedding status of each missing selected study;
- whether any selected study was skipped because prior selection, rather than prior success, defined `already processed`;
- clip-key uniqueness and study-aggregation completeness;
- exact source batch and checksum for every study embedding.

## Historical model artifacts

Vision-only and structured-only scripts historically wrote restricted study-level predictions. The early-fusion LVEF runner wrote aggregate metrics but did not preserve an equivalent prediction table in the frozen snapshot. Multitask runners wrote restricted predictions. Paired inference may therefore require deterministic model-only regeneration on SCC.

## Revalidation artifact rules

Every restricted authority packet should record relative path, byte size, SHA-256, row count, identifier uniqueness counts, source commit, command line, timestamp, MIMIC-IV-ECHO release, Python/package/CUDA versions, and EchoPrime checkpoint identity/checksum. Git receives only aggregate summaries and hashes.
