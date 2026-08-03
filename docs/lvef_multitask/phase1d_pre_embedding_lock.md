# Phase 1D pre-embedding lock

- Branch: `codex/lvef-multitask-revalidation`
- Phase 1D starting commit: `a2ccfb2914546ebd4b7a1f1d369a3e8bc54ad241`
- Historical base: `23c74ccfd145ab9a423b6942a431a1894a34ab67`
- Accepted-abstract authority: version 10, immutable
- Historical primary binary endpoint: `lvef < 40`

## Decision

**The Phase 1D lock is not passed. EchoPrime embedding execution, DICOM redownload/re-extraction, model fitting, prediction regeneration, and confirmatory test-performance access are not authorized.**

Phase 1D is limited to interpreter remediation, aggregate-only evidence availability, canonical-clip inventory, restricted clinical metadata review, and preparation of an embedding go/no-go decision. Accepted ASA abstract version 10, the frozen historical snapshot, and the primary `lvef < 40` endpoint remain immutable.

Status meanings:

- `PASS`: required model-independent evidence is complete for this gate;
- `DISPOSITIONED`: a historical limitation is bounded but not repaired;
- `PROVISIONAL`: the scientific rule is recorded but final implementation identity remains to be proven;
- `PARTIAL`: a design exists but required execution evidence is incomplete;
- `BLOCKED`: evidence or authorization required for use is absent.

## Gate table

| Gate | Status | Current evidence | Required closure evidence | Blocks |
|---|---|---|---|---|
| SCC interpreter authority | `BLOCKED` | Phase 1C used bare `python3`, bypassed the runbook's validated interpreter, and failed under an older SCC system Python at the future-annotations import before clinical packet creation | Python >=3.10 resolver; explicit validated executable; version, executable hash, and numpy/pandas/scipy/sklearn/yaml import status; successful SCC execution | Every Phase 1D Python audit on SCC |
| Batch partition | `PASS` | 4,530 selected; 329 selected Stage-D plus 4,201 exactly-once selected batch studies; 171 Stage-D outside selection; no ownership disagreement | Preserve aggregate evidence and rerun only if source authority changes | Nothing independently |
| Freeze disposition | `DISPOSITIONED` | Old pack has invalid paths/incomplete inventory, no valid comparable checksum entries, and remains immutable | Use independent source hashes and a new preservation manifest for any future run | Historical-pack checksum claim; not a newly validated run |
| Duplicate evidence availability | `BLOCKED` | All 32 selected `batch_000` groups lack complete extracted hashes in the v1 audit | Phase 1D locator/hash/file-survival and metadata availability audit with aggregate safety gate | Interpretation of why groups remain unclassifiable |
| Duplicate classification | `BLOCKED` | All 32 remain `OTHER_UNRESOLVED`; vector equality alone is insufficient | Evidence-aware v2 classification and deterministic resolution rule; restricted per-group evidence retained on SCC | Deduplication/repooling and historical embedding authority |
| Canonical selected clip inventory | `BLOCKED` | No one-row-per-physical-source selected-only inventory exists | Aggregate inventory by Stage-D/batch 000–008, dedup/quarantine counts, exact selected containment, restricted locators | Any clean selected-cohort embedding path |
| Extracted-clip availability | `BLOCKED` | Zero complete extracted hashes in the 32-group v1 audit does not establish cohort-wide extracted-file survival | Cohort-wide current file-existence and safe aggregate availability counts | Path C1 feasibility |
| Source-DICOM availability | `BLOCKED` | Source survival was not established by the v1 duplicate summary | Cohort-wide source-DICOM existence counts without locator export | Path C2 versus C3 choice |
| Embedding path | `BLOCKED` | Scientific preference is a clean selected-only authority; operational form is unknown | Passed duplicate/inventory audits; documented A/B/C1/C2/C3 decision; storage/resource approval; explicit owner authorization | Any embedding/repooling/re-extraction/redownload action |
| Checkpoint identity | `PARTIAL` | Current checkpoint is 138,642,379 bytes, SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`; historical use is unproven | Before a new run, lock this hash or an independently verified replacement and record the decision | Exact historical-reproduction claim and new-run execution |
| Environment capture plan | `PARTIAL` | Required fields are specified, but Phase 1C exposed interpreter ambiguity | Validated interpreter plus prospective package/CUDA/cuDNN/GPU/job/command/config capture and checksums | Reproducible embedding execution |
| Imaging eligibility | `PROVISIONAL` | Five readable-DICOM studies have no multiframe candidate and are excluded from all primary modalities | Apply the rule against the final canonical clip inventory and pass exact denominator identity | Final common cohort |
| Exact-40 counts | `PASS` | Common cohort: 103/2,833 overall and 20/426 test equal exactly 40; no prediction/performance access | Preserve `<40` primary, `<=40` mandatory secondary, `<50` additional secondary | Nothing independently |
| Raw metadata | `BLOCKED` | Phase 1C packet was not generated | Successful supported-interpreter SCC packet with restricted raw rows and allowlisted aggregate summaries | Clinical mapping and panel lock |
| Units | `BLOCKED` | Project units/mixed-unit status remain unreviewed | Native/normalized-unit review and resolution or explicit unresolved disposition per target | Native-unit interpretation and margins |
| Aliases | `BLOCKED` | Exact raw aliases and suspected duplicate exports remain unreviewed | Complete raw-alias review, including LVEF-adjacent fields and exact canonical allowlisting | Leakage masks and merges |
| Dependency registry | `BLOCKED` | Draft formula/correlation distinctions exist; production raw-field coverage is incomplete | Metadata-informed technical and clinical review with evidence-type classification | Strict/family/pragmatic predictor masks |
| Task panels | `BLOCKED` | Three provisional constructs exist; final membership/masks are not locked | Metadata review, support/unit checks, dependency registry, clinician/technical signoff without performance selection | Primary multitask estimand |
| Targeted OpenEvidence | `BLOCKED` | A broad follow-up is prohibited before project metadata review | Generate only if post-metadata `LITERATURE_ANSWERABLE` ambiguities remain; otherwise document that none is needed | External evidence completion only, not dataset identity |
| Clinician signoff | `BLOCKED` | No post-metadata echocardiographer adjudication exists | Mechanically pruned questionnaire plus clinician and technical signatures | Final definitions, panels, and claim boundaries |
| SAP/config checksums | `BLOCKED` | Phase 1D documents remain drafts while upstream gates are open | Final cross-document validation and immutable checksums recorded before execution | Executable analysis authority |
| Owner authorization | `BLOCKED` | No authorization exists for embedding, extraction, redownload, fitting, or test access | Written authorization naming the passed gate record, source commit, chosen path, resource envelope, and checksums | Every mutating or confirmatory action |

## Authorization boundary

Embedding execution can be considered only after the interpreter, duplicate evidence/classification, canonical inventory, source availability, path, checkpoint/environment, safety, checksum, and owner gates pass. Confirmatory modeling additionally requires raw metadata, units, aliases, dependency registry, task panels, common denominators, clinician signoff, final SAP/config, and separate owner authorization.

No successful Phase 1D audit by itself opens either boundary.
