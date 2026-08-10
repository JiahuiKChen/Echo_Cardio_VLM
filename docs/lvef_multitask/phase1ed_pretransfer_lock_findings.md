# Phase 1E-D live pre-transfer lock findings

Status: **NO-GO for DICOM transfer and full C3**.

Recorded: 2026-08-10. This phase sealed and validated one complete read-only `pquota`/`findmnt`/`df`/exact-usage receipt, then produced a separate offline production-specification lock. This phase did not repeat the completed Cloud Storage object listing or storage inventory, contact Google Cloud, submit a scheduler job, download an object body, decode a DICOM, extract a cine, run EchoPrime, create an embedding, fit a model, generate a prediction, or access confirmatory performance.

## Frozen authorities revalidated

The six immutable job-7104307 aggregates and the six supplemental Autoclass attempt-002 aggregates were revalidated in their existing SCC locations by their frozen byte sizes, SHA-256 values, closed schemas, and supplemental dependency graph. All 12 passed. The completed evidence was not copied, regenerated, or modified.

The governing scientific authority remains:

- 4,530 selected studies and 4,530 selected subjects;
- 335,984 metadata-verified selected objects;
- 1,216,569,133,322 selected source bytes;
- zero missing, unexpected, inaccessible, ownership-conflicting, or locator-conflicting selected objects;
- `PASS_CURRENT_SELECTED_SOURCE_INVENTORY_FOR_PROSPECTIVE_C3` for the current public-source inventory;
- `NOT_ESTABLISHED_NO_HISTORICAL_COMPARATORS` for historical-object identity;
- raw Autoclass state `KEY_ABSENT` and effective state `ABSENT_CONFIGURATION_DEFAULT_DISABLED`.

## Sealed Phase 1E-D outputs and attempt lineage

| Aggregate-safe artifact | Attempt | Bytes | SHA-256 | Status |
|---|---:|---:|---|---|
| `lvef_c3_live_quota.summary.json` | 003 | 2,261 | `e0714eb4260973a118a6eab585ba87e55437bc48410b51ceee50182f43c961b9` | `FAIL_LIVE_QUOTA_GATE` |
| `lvef_c3_production_pretransfer_lock.summary.json` | 004 | 11,792 | `94f6df58e21f31e7582bade34527eccd1e14d2300ad1966c8ba67b9da69b1461` | `PASS_SPECIFICATION_ONLY_EXECUTION_UNIMPLEMENTED` |

Attempts 001 and 002 were preserved as failed restricted evidence after, respectively, a safe setgid-directory portability defect and an unsupported native two-line `pquota` header. Attempt 003 then completed all four authorized read-only commands and passed receipt validation. Its later, separate specification step exposed an exact-status consumer defect: the immutable producer receipt says `PASS_SUPPLEMENTAL_ADJUDICATION`, while the first consumer expected generic `PASS`. Attempt 003 was frozen unchanged. The consumer now requires the producer's exact status, generic and near-match statuses fail closed, and attempt 004 contains only the repaired offline specification output. The valid quota commands were not repeated or copied into attempt 004.

## Owner-frozen cost disposition

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

No cost was recomputed or independently reverified in Phase 1E-D. These values close only the planning-cost review gate and remain estimates rather than invoice guarantees.

## Live SCC quota ruling

The standard read-only SCC project-quota report showed:

| Tier | Live quota display | Live usage display | Interpretation |
|---|---:|---:|---|
| backed-up disaster tier | 11 GB | 10.19 GB | A substantial administrative quota reallocation is visible, but this does not prove file migration or backup completion. |
| non-backed-up research tier | 989 GB | 140.04 GB | The additional one-terabyte research allocation is not active. |

Under the established SCC decimal-GB authority, the live research quota is 989,000,000,000 bytes. It is:

- 227,569,133,322 bytes smaller than the selected raw source corpus alone;
- 622,642,076,332 bytes smaller than the frozen 1,611,642,076,332-byte projected peak;
- 822,642,076,332 bytes smaller than the 1,811,642,076,332-byte minimum effective quota, which includes the unchanged 200,000,000,000-byte headroom requirement.

The sealed nonenumerating `du` receipt records 150,386,971,136 exact allocated bytes under the research project root. The project-quota available amount is therefore 838,613,028,864 bytes. The mounted filesystem reports 1,168,126,246,912 bytes capacity, 150,374,187,008 bytes used, and 1,017,752,059,904 bytes available; its independently required available amount is 1,661,255,105,196 bytes, leaving filesystem slack of -643,503,045,292 bytes. The effective capacity ceiling is the smaller project quota, 989,000,000,000 bytes, with effective headroom of -622,642,076,332 bytes against the frozen peak.

Therefore the live capacity gate fails without depending on the rounded usage display. Receipt provenance, command binding, raw-output hashing, and quota-unit interpretation passed; capacity did not. The additional one-terabyte allocation is required before the first selected-DICOM body transfer. If a future live quota is at least 1,811,642,076,332 bytes and the contemporaneous exact-usage/filesystem checks also pass, the existing headroom rule can pass; the preferred nominal research quota remains 2 TB. The headroom rule is not reduced to accommodate the present quota.

The commit-bound `lvef_c3_resource_policy.yaml` remains the historical input authority for the immutable job-7104307 resource calculation. Its preactivation status string is not reused as a statement about the 2026-08-10 live quota, and it is not rewritten merely to make the historical output appear current.

## Backup and migration ruling

Quota reallocation is not data migration. The live 11-GB disaster-tier allocation does not establish that any worktree, checkpoint, environment, audit authority, or irreplaceable restricted record was moved, backed up, or recovery-tested. The checksum-bound planning witness remains `PLANNED_NOT_EXECUTED`; backup completion and migration completion are not asserted.

The existing common Git directory and linked SCC worktrees remain dependencies on the backed-up tier. Before any further disaster-tier reduction or retirement, the project still requires:

1. a checksum-bound completion witness binding the actual migrated and retained bytes to the completed planning classification;
2. an approved disaster-recovery copy of every irreplaceable authority;
3. an independent restore/recovery test;
4. coherent recreation or migration of the common Git repository and both linked worktrees;
5. checkpoint and environment preservation authorities;
6. an after-state quota/filesystem receipt.

No move, deletion, quota change, or backup claim was made in this phase.

## Production orchestration ruling

The offline pre-transfer validator passed as `PASS_SPECIFICATION_ONLY_EXECUTION_UNIMPLEMENTED`. It revalidated all 12 immutable aggregate authorities, all supplied authority-file hashes from their actual files, the exact 19-batch topology, exact source totals, generation-pinned exact-object requirements, requester-pays environment boundary, stage-receipt and no-clobber rules, raw-DICOM retention, one-batch extracted-cache maximum, preservation requirements, and finalization gates. It has no scheduler, cloud, DICOM, extraction, embedding, or modeling execution path.

This is a plan lock, not a production implementation. It retains seven explicit blockers: semantic/source receipt validation is not implemented for production authorities; the live quota/headroom evidence is not yet bound into the lock; backup/migration authority is not yet bound; the production downloader, batch runner, and finalizer are unimplemented; and separate owner transfer authorization is absent. Every existing full-C3 execution entry point remains fail-closed with exit status 78. Consequently, production orchestration is not authorized and cannot be submitted.

## Gate decision

| Gate | Disposition |
|---|---|
| Twelve immutable aggregate authorities | `PASS` |
| Live quota receipt/provenance validation | `PASS_READ_ONLY_CAPTURE` |
| Current selected-source inventory | `PASS_CURRENT_SELECTED_SOURCE_INVENTORY_FOR_PROSPECTIVE_C3` |
| Owner planning-cost disposition | `PASS_OWNER_ACCEPTED_FOR_PLANNING` |
| Live minimum effective research quota | `FAIL_989000000000_LT_1811642076332` |
| Required 200-GB headroom | `FAIL_LIVE_QUOTA` |
| Independent filesystem availability | `FAIL_1017752059904_LT_1661255105196` |
| Backup authority | `BLOCKED_NOT_CHECKSUM_VERIFIED` |
| Migration completion | `BLOCKED_PLANNED_NOT_EXECUTED` |
| Offline production specification | `PASS_SPECIFICATION_ONLY_EXECUTION_UNIMPLEMENTED` |
| Production orchestration execution | `BLOCKED_IMPLEMENTATION_ABSENT_STUBS_FAIL_CLOSED` |
| First DICOM body transfer authorization | `NOT_YET_GRANTED` |
| Full C3 | `NO_GO` |
| Confirmatory modeling | `NO_GO` |

The next bounded phase may implement and smoke-test the production downloader, batch runner, receipts, resume logic, and finalizer without transferring selected-cohort DICOM bodies, while the owner arranges the additional research-tier terabyte and verified backup/migration authorities. No first body request may occur until the additional allocation is active, a fresh exact live-capacity receipt passes, production semantic/source authorities are bound, backup/migration gates pass, and the owner supplies a separate transfer authorization.
