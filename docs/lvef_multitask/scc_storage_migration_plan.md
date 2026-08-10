# SCC storage migration plan for prospective C3

Status: **research expansion is active and passes; the backed control tier remains undersized, and migration/backup-copy completion remains unproven**.

This document concerns storage placement only. It does not authorize a DICOM body transfer, extraction, embedding, modeling, prediction generation, or confirmatory-performance access.

## Observed topology on 2026-08-05

Read-only inspection on `scc4` established the following:

- `/restricted/project/mimicecho` and `/restricted/projectnb/mimicecho` resolve to themselves; neither project root is a symlink.
- They are distinct NFS mount targets and distinct device numbers: `/restricted/project` is device `54` from `scc-fs7vp:/gpfs4/rproject`, while `/restricted/projectnb` is device `56` from `scc-fs7vp:/gpfs4/rprojectnb`.
- No bind mount was observed beneath either project root in the mount table used for this audit.
- Both current SCC worktrees are on `/restricted/project`. The shared Git common directory is `/restricted/project/mimicecho/code/Echo_Cardio_VLM/.git`; the LVEF worktree's `.git` file points back to that common repository.
- Therefore, reducing `/restricted/project` before rebuilding or coherently migrating the Git common directory would break both the LVEF/multitask and LVOT/TAPSE worktrees. A directory-only move of the linked worktree is not sufficient.

The detailed path inventory remains restricted at:

`/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc_20260805/storage/`

It contains path-level names and is not a Git export.

## Aggregate disaster-tier inventory

The read-only `du -x -B1 --max-depth=3` inventory counted 126 directory records and **10,954,752,000 allocated bytes** under `/restricted/project/mimicecho`. This is later and larger than the owner-supplied 8.58-GB usage report, principally because the completed Phase 1E-A smoke and Phase 1D audit authorities now exist. The exact-byte inventory, not the older rounded usage report, controls migration sizing.

| Nonoverlapping top-level class | Allocated bytes | Provisional recovery class | Required disposition |
|---|---:|---|---|
| `code` | 8,113,486,336 | Mostly Git-recoverable or deterministically reinstallable; includes the shared Git authority, a 5,542,192,640-byte EchoPrime environment, and historical output artifacts | Recreate or coherently migrate the shared repository and worktrees; preserve commit/config/environment receipts; do not assume every untracked output is in Git |
| `audits` | 2,277,849,088 | Mixed: public-source-recoverable smoke DICOMs and derivatives, restricted regenerable analyses, and irreplaceable provenance/adjudication records | Preserve exact checksums and copy the authoritative audit packets to an approved backed-up restricted location before reallocation |
| `echoprime_weights` | 489,087,488 | Re-downloadability is not independently proven for the exact candidate checkpoint | Preserve the exact checkpoint and SHA-256 in an approved backup until official provenance is independently resolved |
| `outputs` | 70,198,272 | Mixed restricted derived output, including the separate LVOT/TAPSE project | Treat as a separate project authority; migrate/checksum but do not import into C3 or delete |
| `metadata` | 4,129,792 | Restricted metadata; small but potentially irreplaceable | Preserve in an approved backed-up restricted copy |
| Directory overhead | 1,024 | Filesystem overhead | No scientific action |

The largest observed subtrees were the EchoPrime virtual environment (5,542,192,640 bytes), historical repository outputs (2,414,418,432 bytes), the Phase 1E-A restricted smoke authority (1,731,549,696 bytes), two Phase 1D restricted audit roots (about 270 MB each), and the checkpoint tree (489,087,488 bytes).

## Protection and quota semantics

Current Boston University documentation distinguishes the tiers as follows:

- all Project Disk tiers use RAID and daily snapshots;
- backed-up `/restricted/project` has 21-day user-accessible snapshots and off-site disaster-recovery protection for the most recent 180 days;
- non-backed-up `/restricted/projectnb` has 10-day snapshots but no off-site disaster-recovery copy;
- scheduler-local `/scratch` has no snapshots and is automatically cleaned after about 30 days;
- scheduler `$TMPDIR` is node-local and removed when the job finishes;
- Storage-as-a-Service allocations are sold in whole terabytes defined as 1,000 GB.
- the owner-provided Storage-as-a-Service estimate is accepted for planning: the recorded basis is $22 per TB per year, subject to a six-month minimum and fiscal-year proration, corresponding to $11 for six months or $22 for 12 months for one additional TB. No further cost verification is required for the current project-stage gate.

Sources: [BU Project Disk Space](https://www.bu.edu/tech/support/research/computing-resources/file-storage/proj-diskspace/), [BU storage protection table](https://www.bu.edu/tech/support/research/computing-resources/file-storage/), and [BU job scratch guidance](https://www.bu.edu/tech/support/research/system-usage/running-jobs/resources-jobs/).

All C3 arithmetic therefore uses integer bytes and treats a requested 2-TB quota as `2,000,000,000,000` bytes. Human-readable `df -h` values are binary-formatted displays and are not the allocation authority. The SCC `pquota -u mimicecho` report is the project-quota witness; exact `df -B1` plus filesystem identity and nonenumerating `du -x -s -B1` evidence are separately mandatory for filesystem availability and exact allocated usage.

Immutable parent research attempt `lvef_multitask_phase1ee_post_expansion_capacity_attempt_001` produced `lvef_c3_live_quota.summary.json` (2,257 bytes; SHA-256 `267bf03d8f059b4a71ebe0754015af4a710edea37c060e3e392642e1ad335d71`). Post-expansion composite successor `lvef_multitask_phase1ee_post_expansion_capacity_attempt_002`, bound to implementation commit `0800a0b4de93911cc39467acf2460a8d5ed6135a`, hash-revalidated that parent and captured only contemporaneous control-tier evidence; its 5,003-byte aggregate has SHA-256 `28fad54a68f84165cb8340c3e666de84e1f6efc6bf20b146bc7bc006d9d4171c`. Research quota/usage/available are 1,989,000,000,000 / 150,387,011,072 / 1,838,612,988,928 bytes; physical availability is 2,092,672,483,328 bytes; and file quota/used/available are 33,554,432 / 106,228 / 33,448,204. Research byte, file, minimum-effective-quota, physical-filesystem, and 200-GB-reserve gates pass. The owner-attested administrative composition keeps the purchased 1-TB SAAS allocation wholly assigned to the non-backed research tier; the machine receipt proves the exact effective quota, not its funding source.

The backed tier remains at 11,000,000,000 quota bytes with 10,959,364,608 used and only 40,635,392 available; its physical filesystem has 2,046,820,352 bytes available. Its file quota/used/available are 360,448 / 47,189 / 313,259, so file capacity passes but the control-plane byte-margin gate fails. Capacity evidence does not prove that files were migrated, backed up, or recovery-tested.

## Migration classification and actions

### Git-recoverable

- tracked repository objects that are present on GitHub;
- reproducible worktree checkout state after the final branch is pushed;
- package environments whose exact dependencies, interpreter, and build provenance have been captured.

Action: create a fresh shared clone or bare/common repository under `/restricted/projectnb/mimicecho/code`, recreate both worktrees with their exact branches and commits, verify `git fsck`, `git status`, remote refs, and worktree pointers, and only then retire the old worktree paths. Do not move only `Echo_Cardio_VLM_lvef_multitask` because its `.git` pointer refers to the current `/restricted/project` common directory.

### Public-source-recoverable or deterministically regenerable

- Phase 1E-A downloaded public DICOM bodies after their exact remote object identities are retained;
- extracted technical-smoke arrays and prospective embeddings after preservation verification;
- the Python environment after a fully pinned environment authority is retained.

Action: these may live on `/restricted/projectnb`, but retain their manifests, checksums, command/config identities, and regeneration instructions in a backed-up authority packet.

### Restricted and potentially irreplaceable

- duplicate-key and historical-artifact adjudication records whose original physical sources were purged;
- clinician/technical metadata packets and future signed responses;
- source, checkpoint, environment, and preservation authorities needed to defend the manuscript;
- any untracked restricted output whose generating input no longer exists.

Action: make a separately approved restricted backup (prefer retained `/restricted/project`, restricted STASH, or another institutionally approved archive), generate a safe-relative-path size/SHA-256 manifest, perform a second verification pass, and record the backup location and recovery test. A `/restricted/projectnb` copy alone is not disaster recovery.

### Temporary

- scheduler logs after their aggregate-safe authorities are preserved;
- resumable `.partial` objects after final-object checksums pass;
- rolling extracted NPZ caches after the batch retirement gate passes.

Action: use node-local `$TMPDIR` only for job-lifetime scratch. Place resumable transfer state on `/restricted/projectnb`; never depend on scratch for the sole copy of a completed object or preservation authority.

## Current-state recommendation after research expansion

The live backed quota is 11 GB and is nearly exhausted. **Do not reduce it further, move files, or delete files.** The preferred free-pool adjustment is 50 GB backed and 1,950 GB total research; the minimum option is 25/1,975 GB. Under the owner-attested administrative composition, the purchased 1,000-GB SAAS allocation must remain unchanged on `/restricted/projectnb`. Both options preserve the frozen 1,811,642,076,332-byte research minimum and 200-GB reserve: preferred research slack is 338,357,923,668 bytes, and minimum-option slack is 363,357,923,668 bytes. No quota change is authorized by this plan.

The resource calculator may not use the observed 10,954,752,000-byte historical inventory as the migrated amount. The SCC-only planning classification passed and records 2,841,265,664 bytes selected for planned migration, with state `PLANNED_NOT_EXECUTED`; it is not an executed-migration or backup authority. A future completion witness must bind the exact bytes actually migrated and retained, reconcile them to its contemporaneous source inventory, and state whether migrated bytes are already included in `/restricted/projectnb` usage so they are not added twice.

No further backed-up-tier reduction or retirement may occur until all of the following are true:

1. every restricted path is classified and the restricted path-level inventory is complete;
2. all irreplaceable items have a checksum-verified approved backup outside `/restricted/projectnb`;
3. both Git worktrees are recreated under the destination and a clean checkout/recovery test passes;
4. the exact checkpoint and environment authorities have a verified backup;
5. the Phase 1A-1E historical audit packets and Phase 1E-A preservation authority have a verified backup;
6. RCS activates an approved 50/1,950-GB preferred or 25/1,975-GB minimum free-pool allocation and records the effective decimal-byte quotas;
7. no queued/running job or live environment refers to the old paths.

The owner-accepted SCC storage estimate closes only the planning-cost gate. It does not establish the pending control-tier reallocation, complete the migration/backup witness, authorize a move or deletion, or authorize the first DICOM transfer.

If RCS permits only all-or-none reallocation, the safer sequence is: obtain an approved backed-up copy first, migrate/recreate and verify everything under `/restricted/projectnb`, retain the original until an independent recovery test passes, and only then request the complete exchange. No move or deletion is authorized by this plan.

## Exact recovery and migration checklist

- [x] Preserve and validate the pre-expansion standard `pquota -u` plus nonenumerating exact-usage/filesystem state in the owner-private attempt-003 authority.
- [x] Capture and validate the post-expansion research capacity and successor control-tier evidence in `lvef_multitask_phase1ee_post_expansion_capacity_attempt_002` without repeating research quota/filesystem commands.
- [x] Preserve the historical 126-record restricted inventory and its checksum-bound planning authority.
- [x] Complete the planning-only direct-child classification and seal the 2,841,265,664-byte proposed migration scope.
- [x] Create the checksum-bound planning witness; retain `PLANNED_NOT_EXECUTED`, backup false, and owner execution authorization false.
- [ ] Create and independently verify the backed-up authority copy.
- [ ] Recreate the shared Git repository and both linked worktrees at exact commits.
- [ ] Recreate or relink the EchoPrime environment without changing the pinned interpreter/checkpoint authority.
- [x] Bind substantial future C3 writes, temporary state, scheduler logs, partial downloads, and production state to `/restricted/projectnb`; retain the backed tier only for the bounded control plane.
- [ ] Verify scheduler jobs see the new paths from a compute node.
- [ ] Activate and record the preferred 50/1,950-GB or minimum 25/1,975-GB free-pool allocation without moving the purchased research terabyte.
- [ ] Capture a fresh post-adjustment byte/file/filesystem receipt and complete the restore test.

Research expansion has been captured and passes. A new receipt remains mandatory after the control-tier adjustment; backup, migration, worktree-recovery, and restore-test items remain open.
