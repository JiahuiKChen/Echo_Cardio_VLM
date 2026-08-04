# Phase 1D pre-embedding lock

- Branch: `codex/lvef-multitask-revalidation`
- Phase 1D starting commit: `a2ccfb2914546ebd4b7a1f1d369a3e8bc54ad241`
- Historical base: `23c74ccfd145ab9a423b6942a431a1894a34ab67`
- Accepted-abstract authority: version 10, immutable
- Historical primary binary endpoint: `lvef < 40`

## Decision

**The Phase 1D lock is not passed. EchoPrime embedding execution, DICOM redownload/re-extraction, model fitting, prediction regeneration, and confirmatory test-performance access are not authorized.**

The SCC interpreter and audit wrapper passed. The duplicate audit reached a scientific no-go for the historical store: all 32 groups were classified `SOURCE_ARTIFACT_PURGED` and quarantined. The canonical inventory also failed closed because 4,525 selected imaging studies were expected and seen but only 4,524 had a proposed canonical clip. Only 14,006 Stage-D extracted NPZ sources survived, 170,568 sources were unavailable, and batches 000–008 require DICOM redownload. Path C3 is therefore the conditional preference, but neither redownload nor re-embedding is authorized.

The final clinical audit at commit `e97324a`, run `lvef_multitask_phase1d_clinical_final_20260804T001847Z_e97324a4e4a3`, completed safely with status `COMPLETE_WITH_MISSING_ALLOWLISTED_TARGETS`. The 188-row by 9-column source produced 67 review rows: 30 targets were requested, 29 were present, and `lvef` was the sole missing allowlisted target. Thirty outside-allowlist candidates remain non-authoritative. Seventeen questions remain unresolved, including eight requiring clinician adjudication and zero requiring literature follow-up. The targeted OpenEvidence gate returned `PASS_NO_PROMPT_REQUIRED`. This is a partial metadata disposition, not a panel lock. Accepted ASA abstract version 10, the frozen historical snapshot, the primary `lvef < 40` endpoint, and the passed exact-40 audit remain unchanged.

Status meanings:

- `PASS`: required model-independent evidence is complete for this gate;
- `DISPOSITIONED`: a historical limitation is bounded but not repaired;
- `PROVISIONAL`: the scientific rule is recorded but final implementation identity remains to be proven;
- `PARTIAL`: a design exists but required execution evidence is incomplete;
- `BLOCKED`: evidence or authorization required for use is absent.

## Gate table

| Gate | Status | Current evidence | Required closure evidence | Blocks |
|---|---|---|---|---|
| SCC interpreter authority | `PASS` | Python 3.10.12 passed with executable SHA-256 `1adea0a17d0e729bbd80669793b337f67daa55176be37438bc188fc76b7decdb`; imports passed for numpy 2.2.6, pandas 2.3.3, scipy 1.15.3, scikit-learn 1.7.2, and PyYAML 6.0.3; the subprocess wrapper preserved the parent shell | Preserve the aggregate interpreter and runner records; rerun if the executable or environment changes | Nothing independently |
| Batch partition | `PASS` | 4,530 selected; 329 selected Stage-D plus 4,201 exactly-once selected batch studies; 171 Stage-D outside selection; no ownership disagreement | Preserve aggregate evidence and rerun only if source authority changes | Nothing independently |
| Freeze disposition | `DISPOSITIONED` | Old pack has invalid paths/incomplete inventory, no valid comparable checksum entries, and remains immutable | Use independent source hashes and a new preservation manifest for any future run | Historical-pack checksum claim; not a newly validated run |
| Duplicate evidence availability | `DISPOSITIONED` | The Phase 1D v2 audit completed with its aggregate safety gate and established that retained physical-source evidence is unavailable because the relevant source artifacts were purged | Preserve restricted diagnostics and aggregate counts; do not reinterpret unavailable evidence as physical identity confirmation | Historical-store reuse |
| Duplicate classification | `DISPOSITIONED` | All 32 selected `batch_000` groups are `SOURCE_ARTIFACT_PURGED`; all 32 require quarantine and none is eligible for deterministic deduplication from retained evidence | The historical store remains scientifically unusable as a new authority; closure requires reprocessing through an authorized clean path | Historical clip/study embedding authority and any Path A deduplication claim |
| Canonical selected clip inventory | `BLOCKED` | Audit status `BLOCKED_SELECTED_IMAGING_STUDY_COUNT_MISMATCH`: 4,525 selected imaging studies expected and seen, but only 4,524 have a proposed canonical clip | Restricted resolution of the one-study discrepancy followed by an exact 4,525-study canonical inventory and passed safety gate | Any clean selected-cohort embedding path |
| Extracted-clip availability | `DISPOSITIONED` | Only 14,006 Stage-D extracted NPZ sources survive; 170,568 sources are unavailable, so a complete Path C1 rebuild is not possible | No further availability inference is needed; replacement clips require an authorized DICOM-based path | Path C1 |
| Source-DICOM availability | `DISPOSITIONED` | The inventory establishes that batches 000–008 require DICOM redownload; retained sources are insufficient for a complete C2 rebuild | Resource/governance approval and explicit authorization before any selected-cohort DICOM redownload | Path C2 and immediate execution of C3 |
| Embedding path | `BLOCKED` | Path C3 is the conditional operational preference after the negative duplicate and availability audits; it remains unauthorized and the 4,524/4,525 canonical discrepancy is unresolved | Resolve the canonical-study discrepancy, lock C3 scope/resources/checkpoint/environment, and obtain explicit owner authorization | Any embedding/repooling/re-extraction/redownload action |
| Checkpoint identity | `PARTIAL` | Current checkpoint is 138,642,379 bytes, SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`; historical use is unproven | Before a new run, lock this hash or an independently verified replacement and record the decision | Exact historical-reproduction claim and new-run execution |
| Environment capture plan | `PARTIAL` | Audit-time Python and required-package versions are recorded, but prospective embedding-time PyTorch/CUDA/cuDNN/GPU/job/command/config provenance has not been captured | Capture and checksum the complete environment at an authorized embedding run | Reproducible embedding execution |
| Imaging eligibility | `PROVISIONAL` | Five readable-DICOM studies have no multiframe candidate and are excluded from all primary modalities | Apply the rule against the final canonical clip inventory and pass exact denominator identity | Final common cohort |
| Exact-40 counts | `PASS` | Common cohort: 103/2,833 overall and 20/426 test equal exactly 40; no prediction/performance access | Preserve `<40` primary, `<=40` mandatory secondary, `<50` additional secondary | Nothing independently |
| Raw metadata | `PARTIAL` | The packet and aggregate safety gate passed: 188x9 source metadata, 67 packet rows, 29/30 requested targets present; `lvef` has no exact canonical mapping row and 30 outside-allowlist candidates remain non-authoritative | Adjudicate the missing `lvef` mapping without inventing authority and resolve or explicitly disposition the 17 remaining questions | Clinical mapping and panel lock |
| Units | `BLOCKED` | Twenty-eight targets have one known unit; `ascending_aorta_diameter` has mixed known/unknown unit evidence; `lvef` has no packet rows | Resolve the aortic-unit ambiguity and establish or explicitly disposition `lvef` unit/method metadata | Native-unit interpretation and margins |
| Aliases | `BLOCKED` | Twenty-eight targets have one complete alias; `ascending_aorta_diameter` has multiple aliases with distinct descriptions; `lvef` has no packet rows | Clinically adjudicate the aortic aliases/descriptions and establish or explicitly disposition `lvef` aliases | Leakage masks and merges |
| Dependency registry | `BLOCKED` | The packet completed, but 17 questions remain unresolved, `lvef` mapping is absent, and the eight clinician questions are unsigned | Metadata-informed technical and clinical review with evidence-type classification | Strict/family/pragmatic predictor masks |
| Task panels | `BLOCKED` | Three provisional constructs exist; final membership/masks are not locked | Metadata review, support/unit checks, dependency registry, clinician/technical signoff without performance selection | Primary multitask estimand |
| Targeted OpenEvidence | `PASS` | Zero questions were classified literature-answerable; the gated result is `PASS_NO_PROMPT_REQUIRED`, so no additional prompt was generated | Reopen only if clinician/technical adjudication identifies a genuinely literature-answerable ambiguity | Nothing independently |
| Clinician signoff | `BLOCKED` | Eight mechanically identified clinician questions remain unsigned | Clinician and technical signatures on the restricted adjudication packet | Final definitions, panels, and claim boundaries |
| SAP/config checksums | `BLOCKED` | Phase 1D documents remain drafts while upstream gates are open | Final cross-document validation and immutable checksums recorded before execution | Executable analysis authority |
| Owner authorization | `BLOCKED` | No authorization exists for embedding, extraction, redownload, fitting, or test access | Written authorization naming the passed gate record, source commit, chosen path, resource envelope, and checksums | Every mutating or confirmatory action |

## Authorization boundary

The passed interpreter and clinical-packet safety gates and the completed negative provenance audits do not authorize execution. Embedding execution can be considered only after the 4,524/4,525 canonical discrepancy is resolved, Path C3 scope and resources are locked, checkpoint/environment and safety/checksum requirements are met, and the owner explicitly authorizes DICOM redownload and re-embedding. Confirmatory modeling additionally requires disposition of the missing `lvef` mapping, units, aliases, the dependency registry, task panels, common denominators, clinician signoff, final SAP/config, and separate owner authorization.

No successful Phase 1D audit by itself opens either boundary.
