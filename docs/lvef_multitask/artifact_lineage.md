# Artifact lineage

## Scope and status

This document records what the Phase 1A aggregate SCC audit establishes about the historical LVEF/multitask artifacts. It does not promote an artifact to scientific authority merely because its filename is plausible. Authority requires agreement among checksum, schema, generation lineage, and cohort reconciliation evidence.

The Phase 1A packet used here has SHA-256 `2cdb8dbf01ab37b6ba199db41c8ed45a696b45a6ec32c91189b525bb80ff4c41`. It is aggregate audit evidence generated from commit `62c982bb9fee602cdb7699a6cbebaab3c9852d1c`; it is not a replacement for the restricted source artifacts. The packet safety gate passed with zero issues. The separate SCC preservation pack named `freeze_fullscale_...` is **not yet checksum-validated**.

## Authority classes

| Class | Authority | Permitted use |
|---|---|---|
| Accepted historical abstract | `accepted_abstract_version10_verbatim.txt` | Exact accepted language, cohort descriptions, and reported historical values |
| Git historical aggregate snapshot | `docs/results_snapshot/2026-04-01_fullscale/` | Immutable historical numeric evidence; the Phase 1A SCC preservation-pack checksum was not a checksum test of this Git directory |
| SCC preservation pack | Restricted `freeze_fullscale_...` directory | Candidate byte-preservation authority for historical SCC artifacts; complete `SHA256SUMS` validation currently fails |
| Historical implementation | Scripts at base commit `23c74ccfd145ab9a423b6942a431a1894a34ab67` | Reconstruct intended methods and identify implementation gaps; not proof of the command or artifact instance used |
| Restricted SCC source authority | Split map, manifests, mappings, predictions, embedding stores, environment records, and checkpoint | Resolve lineage and support an authorized revalidation after all gates pass |
| Phase 1A aggregate packet | Aggregate schemas, checksums, counts, equality flags, and discrepancy counts | Audit evidence only; no row-level authority and no confirmatory performance |
| Future revalidation snapshot | A new dated, checksum-manifested aggregate-only result set | Poster/manuscript evidence only after the Phase 1B lock and authorized run |

The accepted abstract and Git historical aggregate snapshot remain immutable historical authorities. The failed checksum applies to the separate SCC preservation pack, not to the Git snapshot. Phase 1A findings annotate limitations; they do not overwrite either source.

## Historical cohort and processing lineage

1. Public MIMIC-IV-ECHO metadata contain 7,243 studies among 4,579 patients. Phase 1A did not receive the public-record or all-eligible-study artifacts, so these remain historical metadata counts rather than newly revalidated packet counts.
2. Historical eligibility required structured-measurement linkage and at least five DICOMs. Prior aggregate recomputation yielded 7,104 eligible studies among 4,530 patients.
3. The selected-study artifact contains 4,530 unique study-subject pairs and passes one-study-per-subject integrity checks.
4. The prior Stage-D manifest contains 500 studies: 329 belong to the selected cohort and 171 do not. Nine subsequent batches contain exactly the remaining 4,201 selected studies, with no duplicate batch assignment or selected-study/subject mismatch. Thus the selected partition is `329 + 4,201 = 4,530`.
5. Download and readability manifests contain every selected study. When the 171 prior-stage studies outside the selected cohort are retained, these artifacts contain 4,701 studies overall.
6. Five selected studies are absent at the first cine-candidate stage. No further selected-study loss occurs through extraction, clip embedding, or study aggregation.
7. The merged clip manifest contains 191,993 rows and 191,961 unique clip keys. The study store contains 4,696 study vectors of dimension 512. Of these, 4,525 studies belong to 4,525 selected subjects and 171 are prior-stage studies outside the selected cohort.
8. Structured measurements contain 669,378 rows for all 4,530 selected studies. The raw registry has 186 names, the canonical registry has 178 names, and the historical known-unit/support panel contains 29 tasks.
9. Exact-name numeric LVEF derivation yields 2,836 selected studies before imaging intersection. Three lack usable study embeddings, leaving the historical 2,833-study LVEF manifest.

## Core artifact identities established by Phase 1A

Paths remain restricted. The aliases below refer to the SCC artifacts inspected by the audit.

| Alias | Shape or rows | SHA-256 | Phase 1A interpretation |
|---|---:|---|---|
| `selected_studies` | 4,530 rows | `920aa8742297dd90c5f125723a425a85201fa7966e926b3191f2c4a57b3d31c1` | Selected one-study-per-subject authority candidate; integrity checks pass |
| `merged_clip_manifest` | 191,993 rows | `b11da49277be683ca3cf5c45104ac99bd86b88eea49b4eebfed2a6d95edbedfc` | Stable merged manifest instance; component-union payload provenance still fails |
| `merged_clip_npz` | `191,993 x 512`, `float32` | `be3fae987a3f1c80842d1bf1e237ee8a641ec8cff27508709e7b489cb9b6e69a` | Stable clip-store instance; provenance gate unresolved |
| `study_embedding_manifest` | 4,696 rows | `feaf0cf7da58ae3d9c8901a583e4319d1b34a8cc26ae57dfcecd309940f81a60` | Matches study-store shape and selected/outside-selected reconciliation |
| `study_embedding_npz` | `4,696 x 512`, `float32` | `f732af46761dae213112bdc32fde88f91e65a27de531ae32edfa619101c01aad` | Stable study-representation instance; historical checkpoint linkage not yet proven |
| `structured_measurements` | 669,378 rows | `95fc852457c25ca548d6fa1ae3ec5d2740b99a6aa3d53297a5b424ffc3d27023` | Structured report source for the selected cohort |
| `lvef_labels` | 2,833 rows | `e28c753dea4e6b2bd678df28222ed4efd53301c568cc1e15a404757a2177b868` | Historical post-imaging LVEF manifest, not a pre-imaging label denominator |
| `subject_split_map` | 4,530 rows | `c5101cea1d76b38c6bb4517edf4b463b338d7505032cfa40bc8f27ca5b97e517` | Split authority candidate; no duplicates, invalid assignments, or overlap detected |
| `raw_canonical_mapping` | 188 mapping rows | `2f1b6c424c62c39017396130fe074accce06cbb05e1977b4c27144e0f824fd18` | Historical mapping instance; clinical dependency adjudication remains pending |
| `strict_tasks` | 29 rows | `941133c29e1c02d6c2f4c798c137126cd1b7cc6697ffcf87a02c0ea238ef6416` | Historical 29-task definition, despite the legacy filename; not the future leakage-minimized panel |

These hashes identify the inspected bytes. They do not, by themselves, prove that every artifact was generated by the intended command, environment, or checkpoint.

## SCC preservation-pack integrity

The complete `SHA256SUMS` validation of the restricted SCC `freeze_fullscale_...` preservation pack failed. The aggregate packet does not identify whether the failure is a missing file, an extra or stale manifest entry, byte mismatch, invalid relative path, or checksum-file formatting defect. This was not a checksum validation of `docs/results_snapshot/2026-04-01_fullscale/`. Consequently:

- the SCC preservation pack cannot yet be described as checksum-validated;
- no file may be edited to make the manifest pass;
- the 14 safe relative filenames exported from that pack are an inventory, not proof of preservation integrity.

Eleven deliberately compared duplicate pairs are byte-identical: five LVEF metric/table artifacts and six manifest/summary artifacts. This is useful file-level evidence but does not cure or explain the full-manifest failure.

## Clip-component union provenance

The Stage-D-plus-nine-batch union was evaluated over all 10 expected component manifests. Component arguments were unique, component count was correct, and source and merged manifests both contained 191,993 successful rows. The audit found:

- identical clip-key sets and multisets;
- 191,961 unique clip keys in each source;
- 32 duplicate clip-key rows in each source;
- zero source-only keys, merged-only keys, or key-multiplicity mismatches;
- identical subject sets, study sets, subject-study pairs, and valid ownership mappings;
- **9,605 clip keys with a non-index row-payload mismatch** and unequal row-payload multisets.

The comparison already excluded `embedding_idx`, so an index rewrite alone cannot explain the failure. Key omission, extra keys, multiplicity, component order, and subject/study ownership are ruled out by the aggregate evidence. Restricted, value-suppressed diagnostics must classify which payload columns and components account for the 9,605 mismatches before the merged store is treated as fully provenance-validated.

## Five selected studies without embeddings

All 4,530 selected studies appear in both download and readable-DICOM stages. Only 4,525 appear in cine candidacy, and those same 4,525 persist through extraction, clip embedding, and study aggregation. Therefore the aggregate attrition assignment is:

| Transition | Selected-study loss |
|---|---:|
| Selection to download | 0 |
| Download to readable DICOM | 0 |
| Readable DICOM to cine candidate | 5 |
| Cine candidate to extracted clip | 0 |
| Extracted clip to clip embedding | 0 |
| Clip embedding to study aggregation | 0 |
| Indeterminate downstream loss | 0 |

`ABSENT_FROM_CINE_CANDIDATES` narrows the stage but does not prove the root cause. It may reflect true absence of usable multiframe cine, manifest logic, or a historical processing omission. A principled imaging-usability exclusion, deterministic reprocessing, and sensitivity treatment must be chosen only after restricted aggregate reason counts establish the cause. Three of the five have numeric LVEF labels.

## Environment and checkpoint provenance

The audited checkpoint file is `echo_prime_encoder.pt`, 138,642,379 bytes, SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`. This proves the identity of the file currently supplied to the audit. It does **not** yet prove that this exact checkpoint generated the historical clip embeddings.

Two frozen metadata artifacts exist and have stable hashes (`freeze_meta_0`: `33108641786d75d34f1641d41070b5ef0256ff34ab4ad421bb15cb8c28addb1f`; `freeze_meta_1`: `3ab76a13fd9952a0d6d0c5cece4359382dd6d0a37620c28018540b2c4f56bce5`). The aggregate packet intentionally did not expose their scalar contents. It therefore does not establish the historical Python, PyTorch, torchvision, CUDA, scikit-learn, or system environment, nor a historical checkpoint-to-embedding link.

Before an authorized revalidation, record a new environment manifest and checkpoint checksum. Unless restricted historical metadata supplies a verifiable link, manuscript language must say that the historical embedding environment and exact checkpoint use could not be independently reconstructed, while separately reporting the fully captured environment for any new run.

## Historical model outputs

LVEF vision-only and structured-only prediction tables are available and have identical subject/study sets and labels on 2,833 studies. The historical early-fusion LVEF runner did not preserve an equivalent prediction table, so three-way paired historical LVEF inference is unavailable without separately authorized deterministic regeneration.

All three multitask prediction tables exist, but structured-only includes up to five selected studies without embeddings that vision-only and fusion cannot include. Historical modality comparisons therefore do not have identical per-task denominators and cannot support paired incremental-value claims as-is.

## Rules for future revalidation artifacts

Every restricted run packet must record relative path, byte size, SHA-256, schema/shape, identifier uniqueness counts, source commit, config checksum, exact command, timestamp, data-release identity, Python/package/CUDA versions, and checkpoint identity/checksum. Every modality comparison must use the same subject-study-target rows. Git receives aggregate summaries and hashes only; restricted manifests, predictions, labels, embeddings, DICOM paths, logs, and lookup tables remain on SCC.
