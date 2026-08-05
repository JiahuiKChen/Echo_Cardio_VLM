# SCC storage migration plan for prospective C3

Status: **planning complete at the filesystem-policy level; migration not executed; exact quota state and backup-copy completion remain gates**.

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

Sources: [BU Project Disk Space](https://www.bu.edu/tech/support/research/computing-resources/file-storage/proj-diskspace/), [BU storage protection table](https://www.bu.edu/tech/support/research/computing-resources/file-storage/), and [BU job scratch guidance](https://www.bu.edu/tech/support/research/system-usage/running-jobs/resources-jobs/).

All C3 arithmetic therefore uses integer bytes and treats a requested 2-TB quota as `2,000,000,000,000` bytes. Human-readable `df -h` values are binary-formatted displays and are not the allocation authority. The SCC `pquota -u mimicecho` report is the required final quota witness because the audited `df` views were not mutually interpretable with the path inventory.

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

## Recommendation on the 200-GB reallocation

**Do not reallocate all 200 GB yet.** If partial transfer is administratively possible, **50 GB is only a provisional backed-up retention option**. It has not been shown to be sufficient. The retained allocation must instead be derived from the completed path-level classification and include every checksum-verified irreplaceable authority plus an approved operating margin; bulk public and regenerable data can remain on `/restricted/projectnb`.

The resource calculator may not use the observed 10,954,752,000-byte inventory as the migrated amount. Before resource sealing, an SCC-only classification witness must record the complete inventory bytes, exact bytes selected for migration, exact bytes retained on the backed-up tier, classification completion, migration state, and checksums of both the inventory and classification decision. The migrated and retained values must reconcile exactly to the inventory. If migration has already occurred, the witness must state that the migrated bytes are already included in the contemporaneous `/restricted/projectnb` usage so they are not added twice.

The full 200 GB can be reallocated only after all of the following are true:

1. every restricted path is classified and the restricted path-level inventory is complete;
2. all irreplaceable items have a checksum-verified approved backup outside `/restricted/projectnb`;
3. both Git worktrees are recreated under the destination and a clean checkout/recovery test passes;
4. the exact checkpoint and environment authorities have a verified backup;
5. the Phase 1A-1E historical audit packets and Phase 1E-A preservation authority have a verified backup;
6. RCS confirms that partial/full quota exchange is supported and records the effective decimal-byte quota;
7. no queued/running job or live environment refers to the old paths.

If RCS permits only all-or-none reallocation, the safer sequence is: obtain an approved backed-up copy first, migrate/recreate and verify everything under `/restricted/projectnb`, retain the original until an independent recovery test passes, and only then request the complete exchange. No move or deletion is authorized by this plan.

## Exact pre-reallocation checklist

- [ ] Save `pquota -u mimicecho` before-state output in the restricted audit root.
- [ ] Freeze the 126-record restricted inventory and SHA-256 it.
- [ ] Classify every non-Git file as recoverable, regenerable, irreplaceable, or temporary.
- [ ] Create the checksum-bound classified migration witness; confirm migrated plus retained bytes equal the complete inventory.
- [ ] Create and independently verify the backed-up authority copy.
- [ ] Recreate the shared Git repository and both linked worktrees at exact commits.
- [ ] Recreate or relink the EchoPrime environment without changing the pinned interpreter/checkpoint authority.
- [ ] Update future C3 commands to `/restricted/projectnb` only; retain no hidden dependency on the old root.
- [ ] Verify scheduler jobs see the new paths from a compute node.
- [ ] Record RCS confirmation of partial-reallocation policy and quota units.
- [ ] Record the after-state quota and restore test.
