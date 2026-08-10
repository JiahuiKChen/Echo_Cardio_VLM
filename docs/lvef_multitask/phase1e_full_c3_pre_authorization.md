# Phase 1E-B/C full C3 pre-authorization

Status: **NO-GO — preparation and metadata-only audits are authorized; the full DICOM body transfer and reconstruction are not authorized**.

This is the controlling pre-authorization record for prospective selected-cohort reconstruction after the passed four-study Phase 1E-A canary. It does not alter accepted ASA abstract version 10, the historical results snapshot, or the historical primary binary endpoint `lvef < 40`.

## Authorization boundary

Institutionally approved restricted-agent analysis permits necessary inspection of restricted schemas, rows, identifiers, paths, DICOMs, arrays, embeddings, predictions, discrepancies, logs, and tracebacks on SCC. It does not convert an otherwise prohibited scientific action into an authorized action. Detailed results remain restricted; only separately reviewed exports may enter Git or a manuscript.

During Phase 1E-B/C the only GCS operation authorized is metadata reconciliation. No object media/body endpoint may be invoked. No full extraction, full EchoPrime inference, predictive model, prediction generation, or confirmatory-performance access is authorized.

The prospective Google Cloud pivot is governed by [`gcp_authority_and_billing_provenance.md`](gcp_authority_and_billing_provenance.md). Historical cloud provenance must not be rewritten. The owner-entered requester-pays value produced `PREFLIGHT_ENV_READY`, which proves only controlled SCC-side capture. It does not prove authentication, project equality, billing linkage, Free Trial credit, requester-pays access, or BigQuery access. Exact prospective account/project values and all credentials remain SCC-only.

## Phase 1E-D owner cost disposition

The owner has accepted the frozen requester-pays and SCC storage estimates for planning. This closes only the cost-planning review gate. It does not authorize a DICOM body request, cloud transfer, quota mutation, storage migration, reconstruction, or modeling.

```text
COST_ESTIMATE_DISPOSITION=OWNER_ACCEPTED_FOR_PLANNING
REQUESTER_PAYS_LOW_ESTIMATE_USD=136.101850
REQUESTER_PAYS_BASE_ESTIMATE_USD=142.906680
REQUESTER_PAYS_HIGH_ESTIMATE_USD=171.488015
REQUESTER_PAYS_HIGH_SCENARIO_ACCEPTED_FOR_PLANNING=YES
SCC_STORAGE_ESTIMATE_ACCEPTED_AS_OWNER_PROVIDED=YES
FURTHER_COST_VERIFICATION_REQUIRED=NO
ACTUAL_DICOM_TRANSFER_AUTHORIZATION=NOT_YET_GRANTED
```

The established planning values are frozen for this phase. No further cost investigation, recalculation, or independent verification is required. Actual charges may differ, but that limitation does not reopen the planning-cost gate. The separate live-quota, backup/migration, production-orchestration, and explicit transfer-authorization gates remain closed.

## Gate table

| Gate | Current disposition | Evidence required to pass | Effect |
|---|---|---|---|
| Prospective cloud tooling resolved | `PASS_PINNED_579_0_0` | Supported Cloud SDK resolved with nonsecret tool provenance | Does not authorize body transfer |
| Prospective Google identity verified | `PASS` | CLI and ADC identities matched the owner-approved SCC-only identity | Does not authorize body transfer |
| Prospective project verified | `PASS` | Configured, requester-pays, and BigQuery project values matched the owner-approved SCC-only project | Does not authorize body transfer |
| Prospective billing link verified | `PASS` | Active billing link verified without exporting billing-account identity | Planning-cost acceptance is recorded separately; transfer authorization remains absent |
| Requester-pays metadata access verified | `PASS_METADATA_ONLY` | Bucket/object metadata probes and the targeted Autoclass receipt passed with zero media/body bytes | Does not authorize body transfer |
| BigQuery billing/access verified | `PASS_DRY_RUN_ZERO_ROWS` | Job-project, MIMIC-IV-ECHO, and MIMIC-IV dry runs passed | Does not authorize row-returning work |
| Free Trial status | `NOT_REQUIRED_FOR_PLANNING_COST_GATE` | The owner accepted the frozen estimate without relying on trial credit as independent authority | Does not authorize transfer or represent a credit-balance guarantee |
| Exact selected-source metadata complete | `PASS_CURRENT_SOURCE_INVENTORY` | 335,984 selected requests metadata-verified; 526-page listing is immutable | Historical object identity remains unestablished |
| Exact selected-source byte total known | `PASS_1216569133322_BYTES` | Integer byte sum over the complete verified request set | Supports resource/cost plan only |
| No unresolved selected-study source deficit | `PASS` | Zero missing/unexpected objects, zero zero-record studies, and zero ownership conflicts | Does not authorize body transfer |
| Storage topology characterized | `PASS` | Distinct devices/mounts, no root symlink/bind, current worktree/common-Git dependencies identified | Does not authorize migration |
| Storage reallocation migration plan complete | `PASS_PLAN_ONLY` | Written classification and exact migration/recovery sequence | Migration execution remains absent |
| Classified migration-byte witness | `ABSENT` | SCC-only checksum-bound witness reconciles complete inventory into exact migrated and retained bytes and records whether migration is already included in current research usage | Blocks authoritative peak arithmetic |
| Backed-up authority plan complete | `PASS_PLAN_ONLY` | Approved destination and checksum/recovery procedure specified | Actual verified backup still blocks reallocation |
| Backed-up authority copy verified | `ABSENT` | Exact safe-relative-path size/SHA-256 manifest and independent restore check | Blocks any further backed-up-tier reduction or retirement |
| Storage expansion administrative status | `PARTIAL_REALLOCATION_OBSERVED_ADDITIONAL_ONE_TB_NOT_ACTIVE` | Standard `pquota` reports 11 GB disaster-tier and 989 GB research-tier allocations; quota state does not prove file migration | Blocks body transfer until the additional research allocation is active |
| Final 2-TB peak leaves required headroom | `FAIL_LIVE_QUOTA_PASS_FROZEN_PLAN` | Frozen peak 1,611,642,076,332 bytes and minimum effective quota 1,811,642,076,332 bytes; current research quota is 989,000,000,000 bytes | Additional one-terabyte allocation and a fresh exact-usage receipt are required |
| Requester-pays planning estimate | `PASS_OWNER_ACCEPTED_FOR_PLANNING` | Frozen low/base/high estimates $136.101850/$142.906680/$171.488015; high scenario accepted | Cost-planning gate closed; body transfer still requires explicit owner authorization |
| Storage-class/Autoclass operation pricing authoritative | `PASS_RATE_EXPLICIT_PLANNING_ESTIMATE` | All objects `STANDARD`; raw key absent; effective state default-disabled; primary rates frozen | Planning estimate, not invoice guarantee |
| SCC storage estimate | `PASS_OWNER_PROVIDED_ESTIMATE_ACCEPTED_FOR_PLANNING` | Owner accepts the supplied SCC storage estimate; no further cost verification is required | Actual `pquota` activation and migration readiness still block body transfer |
| Selected source request manifest frozen | `PASS_STRUCTURAL_ONLY` | Existing hash-locked 4,530-study, 335,984-request authority | Not yet public-object authority |
| Enriched selected public-object inventory frozen | `PASS_CURRENT_INVENTORY` | Immutable job-7104307 outputs plus supplemental source-authority/provenance/safety chain | Does not establish historical byte identity |
| Checkpoint identity frozen | `PASS_CANDIDATE` | 138,642,379-byte checkpoint with SHA-256 `7ca32e...e64f3b` | Historical linkage remains unclaimed |
| Production environment contract frozen | `PASS_SPECIFICATION_ONLY` | Python 3.10.12, PyTorch 2.11.0+cu130, torchvision 0.26.0+cu130, CUDA/cuDNN/GPU capture, script/config identities | Full-run capture still required |
| Production preprocessing/cine contract frozen | `PASS_SPECIFICATION_ONLY` | Explicit multiframe candidacy, decoder/color/mask/signal gates, deterministic temporal sampling, and reference-transform claim boundary are machine-validated in the contract | Implementation/equivalence evidence still blocks full C3 |
| Production streaming/batch implementation validated | `ABSENT` | Generic exact-generation downloader, streaming batch implementation, preservation receipts, smoke equivalence, resume/failure tests, and scheduler dry run | Blocks full C3 |
| Technical metadata review complete | `PENDING_RESTRICTED_AUDIT` | All nine fixed issues receive evidence-bound dispositions without performance access | Does not block outcome-blind imaging reconstruction; blocks modeling |
| Echocardiographer packet ready | `PASS_IMPLEMENTATION_PENDING_RESTRICTED_GENERATION` | Exact eight-question checksum-bound packet builder, response template, and validator pass synthetic tests | Human signoff still separate |
| Echocardiographer signoff complete | `ABSENT` | Qualified reviewer, eight responses/rationales, checksum/date/role | Blocks final panels/modeling, not imaging reconstruction |
| Direct restricted-agent mode documented | `PASS_IMPLEMENTED` | Approved-root enforcement, restricted run receipts, and synthetic path/authority tests | Does not grant prohibited scientific actions |
| Git/manuscript export gate documented | `PASS_IMPLEMENTED` | Two-stage exact-hash approval, schema/value safety, staged-Git gate, and synthetic tests | Every actual export still needs separate human review |
| Command/config checksums frozen | `PASS_SUPPLEMENTAL_ONLY` | Supplemental parser, validator, policies, receipts, and manifest are checksum-bound | Production C3 command/config authority still blocks full C3 |
| Full C3 owner authorization | `ABSENT` | Written authorization naming commit, config/checksums, source, resources, commands, checkpoint, environment, and preservation contract | Absolute block |

## Current scientific cohort authority

- selected studies: 4,530;
- selected subjects: 4,530;
- policy: one selected study per subject;
- structural source rows: 336,016;
- normalized public-source requests: 335,984;
- identical repeated-locator excess rows collapsed: 32;
- outside-selected studies permitted: zero;
- historical embeddings or clips reused: zero;
- full-release mirror required: no.

The five historical studies with readable DICOM but no multiframe cine candidate remain provisionally imaging-ineligible, not unexplained processing failures. Prospective C3 must regenerate denominator evidence without using labels or performance. A new candidate decode/extraction failure is a technical blocker and may not be reclassified as no-cine attrition.

## Storage ruling

The preferred lifecycle is full selected raw retention plus a one-batch rolling extracted cache. `/restricted/project` and `/restricted/projectnb` are distinct devices. Both existing worktrees and their common Git repository are on the backed-up tier, so quota exchange requires worktree recreation or coherent common-repository migration. The exact observed disaster-tier inventory was 10,954,752,000 allocated bytes.

The historical 50-GB retention option was provisional and never established sufficiency. The live backed-up allocation is now only 11 GB, with 10.19 GB displayed usage. It must not be reduced further. The quota change does not establish file migration, backup-copy completion, or recovery testing; those remain blocked pending a complete checksum-bound path classification, an approved disaster-recovery copy of every irreplaceable authority, and recovery tests for both worktrees.

## LVEF authority ruling

The absence of an exact `lvef` row from the 188-row canonical mapping is expected and must not be repaired with a synthetic mapping row. The historical LVEF anchor is governed separately by:

1. exact case-sensitive raw measurement name `lvef`;
2. numeric `result` parsing;
3. median aggregation by `(subject_id, measurement_id)`;
4. selected-study linkage in `build_lvef_still_manifest.py`;
5. the passed independent label-provenance audit.

This establishes label identity and aggregation. It does not by itself establish a single acquisition method, homogeneous units, or the absence/equivalence of EF-adjacent aliases. Those are technical metadata questions and fail closed for structured/fusion predictor eligibility until the direct SCC review finishes.

The endpoint authority remains:

- historical primary binary: `lvef < 40`;
- prespecified secondary boundary sensitivity: `lvef <= 40`;
- guideline-classification sensitivity: `lvef < 50`;
- continuous LVEF: primary scientific anchor.

Exact-40 counts were obtained before confirmatory access: 103 of 2,836 selected linked labels, 103 of 2,833 common imaging-eligible labels, and 20 of 426 common test labels equal exactly 40. These are label-count audits, not performance results.

## Technical metadata and clinical signoff

The direct SCC technical audit must disposition exactly nine issues: BSA formula/weight, dimension units, LVEDV/LVESV fields, LVEF aliases, LVEF method mixture, LV mass/RWT, mitral ratios, velocity units, and wall-motion fields. Training-only distributions are permitted for unit plausibility; predictions and performance inputs are prohibited.

The human packet must contain exactly the eight prespecified echocardiographer questions. Restricted-agent access may prepare and quality-control it, but may not substitute for qualified human signoff. Until the packet is completed, all ambiguous targets and cross-family predictors fail closed and provisional panels remain unlocked.

## Prepared execution contract

[`lvef_c3_execution_contract.yaml`](../../configs/lvef_c3_execution_contract.yaml) freezes the intended future authority without granting it. It binds the 4,530-study selected cohort and MIMIC-IV-ECHO 1.0 source; requester-pays configuration through an SCC-only environment variable; `/restricted/projectnb/mimicecho/lvef_multitask_c3` output roots; 250-study deterministic chunks yielding 19 production batches; concurrency one; exact-generation, size, MD5, and local SHA-256 requirements; object-atomic partial files; header-first multiframe candidacy; deterministic extraction; the 138,642,379-byte EchoPrime checkpoint and SHA-256; the prospective Python/PyTorch/CUDA contract; raw-DICOM retention; manifest-exact extracted-cache retirement gates; unique physical-source keys; no historical embedding reuse; no outside-selected studies; float32 512-dimensional clip vectors; stable float64-accumulated study mean pooling; denominator and preservation authorities; direct restricted-agent receipts; and reviewed export gates.

The contract now also makes the three Google Cloud provenance classes machine-checkable: preserved-or-unknown historical association, an SCC-only active prospective identity/project authority, and nonexportable credential state. It requires identity, project, billing-link, requester-pays metadata, and BigQuery billing gates before future execution. The exact prospective account/project values are intentionally absent from Git.

The scheduler interface prints a future 19-task SGE array (`-t 1-19 -tc 1`) and dependent finalizer without exporting the ambient environment. Every current production entry point exits 78, and `--submit` is refused. Before that interface can be authorized, the repository still needs a generic exact-generation downloader, streaming batch runner, batch preservation/audit receipts, cross-batch finalizer, selected-source freeze, migrated/revalidated environment, Phase 1E-A equivalence tests, resume/failure tests, command/config manifest, live quota and migration authority, and explicit owner transfer authorization. The four-study smoke implementation cannot be relabeled as the production runner.

## Full C3 decision

**NO-GO.** The current phase may complete source metadata, resource arithmetic, restricted technical metadata review, packet preparation, implementation, and dry-run validation. The following remain absolute prerequisites before a later owner authorization can be considered:

1. contemporaneous live research-quota, filesystem, and integer-byte usage evidence proving at least 1,811,642,076,332 effective bytes under the current plan (preferred nominal allocation 2 TB);
2. contemporaneous quota and usage arithmetic consistent with the owner-accepted planning envelope; the cost-planning gate itself is closed;
3. verified backup/migration readiness and final quota activation;
4. production exact-generation download, streaming batch, preservation, and finalization implementation plus smoke-equivalence/resume/failure tests;
5. frozen production source, command, config, checkpoint, and environment identities;
6. written owner authorization for the first DICOM transfer and full C3; neither has been granted.

Confirmatory modeling additionally requires qualified clinician signoff, final aliases/units/dependencies/panels, exact common denominators, final SAP/config checksums, and a separate explicit owner authorization.
