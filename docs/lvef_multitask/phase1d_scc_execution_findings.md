# Phase 1D SCC execution findings

## Authority and safety boundary

This document is the aggregate-only authority record for the Phase 1D SCC execution and the final clinical-metadata retry. It reports counts, fixed-vocabulary dispositions, runner status, and authorization effects. It contains no raw measurement metadata, patient or study identifiers, clip keys, DICOM or NPZ locators, row-level hashes, labels, predictions, embeddings, or performance results.

| Execution authority | Commit | Restricted audit root | Role |
|---|---|---|---|
| Initial Phase 1D execution | `912250174f99bdc977e902e14f9d2881a110438c` | `/restricted/project/mimicecho/audits/lvef_multitask_phase1d_20260803T234842Z_912250174f99` | Authoritative portability, duplicate-provenance, and canonical-inventory execution; initial clinical attempt failed closed. |
| Final clinical-metadata execution | `e97324a4e4a313e73cb0b46446f7be18d8fbbe1f` | `/restricted/project/mimicecho/audits/lvef_multitask_phase1d_clinical_final_20260804T001847Z_e97324a4e4a3` | Authoritative successful clinical-metadata packet and targeted-follow-up gate. |

Both commits are on `codex/lvef-multitask-revalidation`. The audit interpreter and isolated runner passed. The runner preserved nonzero child status, kept the parent interactive shell alive, and exported only allowlisted aggregate outputs. These facts establish execution control for these audits; they do not establish historical EchoPrime-generation provenance or authorize any downstream action.

## Executive disposition

The Phase 1D model-independent audits completed, but the embedding and confirmatory locks remain **NO-GO**.

- All 32 duplicate groups are `SOURCE_ARTIFACT_PURGED`; zero are eligible for deterministic deduplication and all 32 remain quarantined.
- The selected inventory contains 4,525 studies with historical embedding rows but only 4,524 studies with at least one proposed nonquarantined canonical clip. The five separately established no-cine studies remain without embedded clips.
- Only 14,006 Stage D extracted NPZ physical sources survive. The other 170,568 physical sources, in batches 000–008, retain neither NPZ nor DICOM and require an authorized redownload and re-extraction path.
- C1 and C2 are unavailable. C3 is the preferred clean prospective path, but it is not authorized.
- The final clinical-metadata audit passed its aggregate safety gate and produced a restricted review packet. It resolves the tooling blocker, not the clinical definitions, unit questions, dependency registry, task panels, or owner-authorization gates.

No model fitting, prediction regeneration, test-performance access, DICOM download, re-extraction, re-embedding, repooling, historical-store mutation, or confirmatory use is authorized by these results.

## Interpreter and isolated-runner result

| Finding | What it proves | What it does not prove | Authorization effect |
|---|---|---|---|
| Interpreter preflight: `PASS` | A supported project interpreter was selected and the required dependency imports and dependency-light tests completed under the recorded audit environment. | It does not capture the future embedding-time Python, PyTorch, scikit-learn, CUDA/cuDNN, GPU, command, scheduler, or checkpoint lineage. | Closes the Phase 1D audit portability gate only. |
| Isolated runner: `PASS` | Committed Bash blocks were syntax-checked and run in guarded child processes; child failures remained visible and the parent shell survived. Safe-output allowlists remained enforced. | It does not convert a failed child audit into success, validate restricted scientific content by itself, or authorize mutation. | The successful audit outputs may be interpreted according to their individual gates. |

## Duplicate-provenance adjudication

| Aggregate result | Count |
|---|---:|
| Duplicate groups reviewed | 32 |
| `SOURCE_ARTIFACT_PURGED` | 32 |
| Eligible for deterministic deduplication | 0 |
| Required quarantine | 32 |

For every group, stored embedding-vector, L2, normalized manifest/extraction-metadata, and component/merged evidence were concordant. The retained source DICOM and extracted NPZ content or physical-file hashes required to confirm physical-source identity were unavailable. This establishes that serialization and expected index rewriting are not the remaining disagreement and that the retained evidence is insufficient for deduplication.

It does **not** establish that the rows are exact physical duplicates, that the same clip was embedded twice, that different clips share a key, or that a merge defect is the cause. Vector and metadata concordance cannot substitute for physical-content evidence. Consequently, the historical merged clip and study stores remain blocked, Path A is prohibited, and no duplicate row may be silently removed.

## Canonical selected-source inventory

| Quantity | Aggregate count | Interpretation |
|---|---:|---|
| Selected studies seen in embedding manifests | 4,525 | Historical selected-cohort imaging rows exist for these studies. |
| Selected studies with at least one proposed nonquarantined canonical clip | 4,524 | Exact selected imaging authority is not restored; the one-study shortfall remains blocking. |
| Selected studies without embedded clips | 5 | The previously adjudicated readable-DICOM/no-multiframe-cine studies remain provisionally imaging-ineligible. |
| Selected embedded manifest rows | 184,606 | Historical selected rows before physical-source grouping and quarantine. |
| Outside-selected rows excluded | 7,387 | These rows cannot enter the one-study-per-subject authority. |
| Selected physical-source groups | 184,574 | Aggregate physical-source grouping count before final authority promotion. |
| Physical sources with surviving extracted NPZ | 14,006 | All are in Stage D and still require cohort-wide readability, schema, content, and source-mapping validation. |
| Physical sources with neither retained NPZ nor DICOM | 170,568 | These are in `batch_000`–`batch_008`; replacement requires DICOM redownload and re-extraction. |
| Quarantined physical-source groups | 32 | None can be deterministically deduplicated from retained evidence. |

The inventory proves the aggregate extent and component location of retained versus unavailable physical sources. It does not prove that every surviving Stage D NPZ is readable, schema-conformant, content-valid, or correctly mapped to its public source. It also does not create a canonical row-level source manifest or resolve the 4,524/4,525 study discrepancy.

The five no-cine studies remain excluded from all modalities in primary paired comparisons. A future structured-only full-availability sensitivity would use a distinct denominator and cannot support paired modality claims.

## Embedding-path ruling

| Path | Phase 1D result | Authorization effect |
|---|---|---|
| A: deterministic deduplication/repooling | `PROHIBITED` | All 32 groups lack physical-content confirmation. |
| B: affected-batch retained-source repair | `UNAVAILABLE_AS_RETAINED-SOURCE_REPAIR` | The affected batch source artifacts are purged; a mixed newly generated/historical store would not repair the broader provenance limitation. |
| C1: selected-only re-embedding from retained NPZs | `UNAVAILABLE` | Only 14,006 Stage D NPZ sources survive; 170,568 sources lack NPZs and quarantine remains. |
| C2: selected-only re-extraction from retained DICOMs | `UNAVAILABLE` | Retained DICOMs are absent for batches 000–008. |
| C3: clean selected-only prospective reconstruction | `PREFERRED_BUT_UNAUTHORIZED` | Validate Stage D authority, redownload and re-extract selected sources for batches 000–008, and re-embed the complete selected imaging-eligible set under one pinned environment only after explicit approval. |

C3 is a provenance recommendation rather than permission to execute. If Stage D validation fails, its corresponding source scope must also be redownloaded and re-extracted. Repeated-study or all-study expansion is outside this decision.

## Final clinical-metadata audit

| Aggregate item | Final result |
|---|---:|
| Source schema | 188 rows x 9 columns |
| Restricted review-packet rows | 67 |
| Allowlisted targets requested | 30 |
| Allowlisted targets present | 29 |
| Missing exact allowlisted mapping | `lvef` |
| Candidate canonical mappings outside the allowlist | 30 |
| Unresolved issue classes | 17 |
| Clinician-adjudication questions | 8 |
| Literature-answerable questions | 0 |
| Aggregate clinical safety gate | `PASS` |
| Targeted OpenEvidence result | `PASS_NO_PROMPT_REQUIRED`; no prompt generated |

The 30 requested targets comprise the LVEF anchor plus the exact `legacy29` allowlist. The missing exact `lvef` mapping is a documented source-mapping absence, not permission to invent or infer a mapping. The final audit correctly records `COMPLETE_WITH_MISSING_ALLOWLISTED_TARGETS` and `missing_target_mapping_is_registry_authority = false` instead of treating that absence as a program failure.

The audit proves that the 188-by-9 mapping source was read through the expected schema, that exact identifier allowlisting selected 67 restricted review rows for 29 present targets, that 30 outside-allowlist canonical candidates were counted but not promoted, and that the fixed-vocabulary ambiguity classifier and aggregate safety checks completed. The 30 outside candidates are discovery candidates only; their names and metadata remain restricted and they are not registry authority.

The audit does **not** prove that raw names are aliases, that units are correct or homogeneous, that method/view/timing definitions are clinically equivalent, that formula or target-family relationships are final, or that any panel is ready. The 17 unresolved issue classes remain unresolved, including eight requiring clinician adjudication. The zero literature-answerable count means the mechanical evidence routing found no question suitable for a targeted OpenEvidence prompt at this stage; it does not mean that all clinical questions are answered. No broad or substitute OpenEvidence prompt should be issued from this result.

## Fail-closed clinical retry chain

The successful packet followed three transparent tooling corrections. Every failed attempt returned nonzero, preserved the parent shell, emitted no clinical review packet from that attempt, and left the scientific gates closed. Each correction was covered by synthetic tests before the corresponding retry.

| Stage | Observed issue | Why it was not scientific evidence | Fail-closed behavior and correction |
|---|---|---|---|
| Initial execution at `912250174f99bdc977e902e14f9d2881a110438c` | Absence of an exact canonical `lvef` row was incorrectly treated as fatal. | LVEF is a separately governed anchor, and absence from this raw-to-canonical mapping cannot be repaired or interpreted by the audit. | The child failed, no packet was accepted, and the parent shell survived. Commit `5a6298366c3a08f3112c0e51668555af598c923e` changed the audit to record missing allowlisted targets explicitly as nonauthority. |
| First safety retry | The aggregate safety scan treated serialized JSON keys and CSV headers as emitted restricted data, producing false positives. | Schema keys and allowlisted aggregate headers are structural metadata, not restricted row values. | The child again failed with no accepted packet. Commit `b6eb2ffdf669a4c8e002398d1d8534df9ba45f33` restricted scanning to JSON leaf values and table cell values while retaining allowlist and schema checks. |
| Second safety retry | A short restricted raw alias appeared as a substring of an exact allowlisted canonical target, producing another false positive. | Exact canonical target identifiers are sanctioned aggregate values and are independently constrained by exact target-set equality. | The child failed with no accepted packet. Commit `e97324a4e4a313e73cb0b46446f7be18d8fbbe1f` exempted only exact allowlisted target cell values from substring scanning; unknown and nonallowlisted values remain blocked. |
| Final clinical execution | The clinical packet and both safety gates passed; zero literature-answerable issues were present. | Passing execution is not clinical adjudication or registry approval. | The final root above is the only successful Phase 1D clinical-packet authority. No OpenEvidence prompt was generated. |

These corrections narrowed mechanical false positives; they did not weaken the canonical allowlist, admit unknown identifiers, expose restricted metadata, or convert missing mappings into authority.

## Authorization effects and remaining gates

| Finding | Gate effect |
|---|---|
| Interpreter and isolated runner passed | Portability/runner gate closed for these audit executions only. |
| Duplicate audit completed with 32 quarantines | Historical clip/study embedding authority remains blocked; deterministic deduplication remains prohibited. |
| Canonical inventory completed with 4,524 proposed studies | Selected-source authority and exact common denominator remain blocked pending restricted resolution and a future clean reconstruction. |
| Stage D/batch availability was bounded | C1 and C2 are ruled out; C3 may be planned but not run. |
| Clinical metadata packet passed safely | The tooling-execution blocker is closed; restricted technical and clinician review may proceed. |
| Seventeen unresolved issues and eight clinician questions remain | Dependency registry, unit/alias rulings, task panels, and clinical signoff remain blocked. |
| Zero literature-answerable issues | No targeted OpenEvidence follow-up is currently indicated; this does not close clinical review. |

Before any C3 execution, the project still requires restricted resolution of the 4,524/4,525 canonical-study discrepancy, Stage D source/readability/schema/content validation, a selected-only public-source manifest, resource and data-access approval, a pinned checkpoint and complete extraction/embedding environment, exact commands and preservation schema, and written owner authorization.

Before any confirmatory model or test access, the project additionally requires completed technical and clinician adjudication of the restricted metadata packet, final unit/alias/dependency decisions, locked task panels and common denominators, final SAP/config identities and checksums, and separate explicit owner authorization.

The accepted ASA abstract version 10, frozen historical results, and historical primary endpoint `lvef < 40` remain unchanged.
