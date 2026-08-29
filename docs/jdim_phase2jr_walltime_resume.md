# JDIM Phase 2J-R2 Wall-Time-Safe Continuation

This runbook is limited to resuming the locked 4,808-file restoration request,
publishing the unchanged technical inventory, and building the blinded audit
interface. It does not rerun a scientific analysis or alter the locked roster.

## Fixed execution policy

- Authorized parent: `229a3a88b040eb8728e7f1e73a43711a704c4d7f`.
- The SCC source must be exactly one clean commit above that parent.
- An expected zero-byte regular `.part` is tracked separately as
  `RESTARTABLE_ZERO_PLACEHOLDER`; symlinks, directories, overlaps, alternate
  paths, and unexpected objects still fail closed.
- Restoration uses at most two transfers, ordering nonzero partials, zero-byte
  placeholders, then missing files. It stops starting work at 38,700 seconds
  within an 11:30:00 allocation.
- Restricted progress is updated after each transfer. Aggregate-safe state is
  checkpointed every 25 completions or 15 minutes.
- A2 may issue `LOCKED_ROSTER_SOURCE_RESTORED` or
  `RESTORATION_INCOMPLETE_RESUMABLE`. A3 verifies A2 and either no-ops,
  continues, or issues the bounded final blocker.
- Each downstream stage verifies the exact upstream job number and requires its
  semantic `qacct` state to agree with the certificate before proceeding.
- B1 requires the hash-bound restoration certificate. Technical inventory is
  staged as one immutable bundle, and media rendering reuses every valid
  completed opaque media item.
- B2 requires either B1 readiness or a valid resumable checkpoint. It validates
  and no-ops when B1 completed.

## SCC entry point

After the exact committed SHA has been pulled into the clean SCC checkout, run:

```bash
export JDIM_PHASE2JR_SOURCE_COMMIT=<exact-committed-sha>
scripts/scc_submit_jdim_phase2jr_chain.sh
```

The submitter runs the full test suite, parses Job 7354017 accounting by exact
field name, verifies all pinned hashes, and confirms absence of prior completion
certificates. Before creating a new output root, its read-only disk assessment
must return `RESTORATION_STATE_RESUMABLE` with exactly 2,342 complete files, 4
nonzero partials, 2,111 zero-byte placeholders, 351 missing files, and no invalid
or unexpected objects. It then creates V3b/V3c run roots and submits only the
serial A2 to A3 to B1 to B2 dependency chain.

Do not use a job array, add unrelated dependencies, expose `.netrc`, poll via a
browser automation, or modify prior output roots. A2 and A3 use the immutable
V3b and V3c roots; the persistent interface remains audit-interface V2.
