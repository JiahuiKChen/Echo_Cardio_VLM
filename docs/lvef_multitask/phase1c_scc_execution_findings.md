# Phase 1C SCC execution findings

## Authority and safety boundary

This record interprets the complete Phase 1C SCC terminal transcript supplied for Phase 1D. The run began from commit `a2ccfb2914546ebd4b7a1f1d369a3e8bc54ad241` on `codex/lvef-multitask-revalidation`. It executed only model-independent partition, provenance, duplicate-key, exact-threshold, and attempted clinical-metadata audits.

No model was fit or tuned. No prediction table, confirmatory performance metric, new EchoPrime embedding, or image-derived content was generated or inspected. The accepted ASA abstract version 10, the frozen historical snapshot, and the historical `lvef < 40` endpoint were not altered.

The provenance and exact-threshold audits completed before the later clinical-metadata failure and remain valid aggregate evidence. The clinical metadata packet was not generated.

## Execution results

| Execution item | Result | What it proves | What it does not prove | Gate effect |
|---|---|---|---|---|
| Selected batch partition | Passed: 4,530 selected studies; 500 Stage-D studies, of which 329 are selected and 171 are outside selection; all 4,201 selected-minus-prior studies occur exactly once across batches 000–008; no assignment or ownership discrepancy | The historical selected-cohort partition remains internally complete and one-study-per-subject ownership remains consistent | It does not validate physical clip identity, surviving source files, the mixed embedding store, or a future embedding run | Batch-partition gate passes; embedding authority and confirmatory access remain closed for independent reasons |
| Duplicate-key audit | Completed with blocking result: 32 unique duplicate groups, all selected, all in `batch_000`; zero groups have complete extracted-file hashes; all 32 were classified `OTHER_UNRESOLVED` with proposed action `QUARANTINE_AND_REVIEW_OR_REPROCESS_AFFECTED_CLIPS`; aggregate safety passed | The duplicate count, component, selected scope, and retained-evidence insufficiency are established without exporting identifiers | It does not prove corruption, physical identity, harmless duplication, key collision, purge as the cause of missing hashes, or eligibility for deterministic deduplication | Blocks historical clip/study embedding authority, embedding-path choice, and all confirmatory vision/fusion access |
| Exact-LVEF-40 audit | Passed over both prespecified cohorts and all splits; no prediction or performance file was read | Exact counts at the historical threshold are now known before test-performance access and from the historical median-label derivation | It does not estimate AUROC, sensitivity, specificity, calibration, or any model effect; it does not authorize changing the endpoint | Endpoint-count gate passes; historical `<40` remains primary and `<=40` remains a mandatory secondary sensitivity |
| Clinical metadata audit | Did not start: `scc_phase1c_clinical_metadata_commands.md` invoked bare `python3`, bypassing the runbook's validated `PYTHON_BIN`; the resulting older SCC system interpreter raised `SyntaxError: future feature annotations is not defined` at `from __future__ import annotations` | The committed Phase 1C command had an interpreter-selection defect and the resolved system interpreter could not parse the script | It does not identify any raw alias, description, unit, mapping ambiguity, or clinical relationship; it is not evidence that the script or future import should be weakened | Blocks raw-metadata, unit, alias, dependency-registry, task-panel, clinician-review, and targeted-OpenEvidence gates |

## Exact-threshold counts

| Cohort scope | Split | Observed numeric LVEF | Exactly 40 |
|---|---|---:|---:|
| Selected pre-imaging | All | 2,836 | 103 |
| Selected pre-imaging | Train | 1,998 | 71 |
| Selected pre-imaging | Validation | 411 | 12 |
| Selected pre-imaging | Test | 427 | 20 |
| Primary common imaging-eligible | All | 2,833 | 103 |
| Primary common imaging-eligible | Train | 1,997 | 71 |
| Primary common imaging-eligible | Validation | 410 | 12 |
| Primary common imaging-eligible | Test | 426 | 20 |

Thus 103 of 2,833 common imaging-eligible LVEF labels, including 20 of 426 test labels, equal exactly 40%. The distinction between `<40` and `<=40` is materially nontrivial. This was established from labels alone before prediction or performance access. It preserves, rather than replaces, the accepted abstract's strict `<40` primary definition.

## Why the interactive SSH shell terminated

The Phase 1C instructions were sourced into an interactive Bash shell that already had `set -euo pipefail`. When the clinical Python command returned nonzero, Bash applied `errexit` to the sourced command body and terminated the parent interactive session. The SSH termination therefore followed the nonzero sourced command; it was not evidence that the earlier completed audits were rolled back or invalid.

Phase 1D must execute each committed audit block in its own syntax-checked subprocess, capture its status and restricted logs, and keep the parent shell alive. A nonzero child status must remain visible and blocking.

## Portability ruling

The exact failure was an interpreter-resolution defect in the command document: its two bare `python3` calls bypassed the runbook's `PYTHON_BIN` authority and selected an older SCC system interpreter. The project requires Python 3.10 or newer and must prefer the existing EchoPrime environment at `/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python`, subject to version and package validation. An explicit `LVEF_SCC_PYTHON` override is permissible. Silent fallback to an older `python` or `python3` is prohibited.

The `from __future__ import annotations` line remains unchanged. Removing it would conceal an unsupported runtime instead of fixing authority and reproducibility.

## Duplicate-evidence interpretation

The absence of complete extracted hashes in all 32 groups means only that the v1 classifier lacked complete physical-file evidence. It does not show that the files were corrupt, unequal, or purged. The next restricted audit must separate:

1. locator and retained-hash availability;
2. current source/extracted file existence and newly computable hashes;
3. manifest, frame/extraction metadata, and component/merged vector correspondence;
4. duplicate classification; and
5. the evidence-specific remediation recommendation.

Vector equality alone cannot establish that two rows refer to one physical clip. Deterministic deduplication requires concordant ownership, source identity, extraction identity, and payload evidence.

## Current authorization decision

The Phase 1C lock is not passed. Phase 1D aggregate-only remediation audits are allowed, but embedding generation, DICOM redownload, model fitting, prediction regeneration, and confirmatory performance access remain unauthorized until their separate gates and explicit owner authorization are recorded.
