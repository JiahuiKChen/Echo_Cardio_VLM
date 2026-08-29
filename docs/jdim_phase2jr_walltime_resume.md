# JDIM Phase 2J-R Wall-Time-Safe Continuation

This runbook is limited to resuming the locked 4,808-file restoration request,
publishing the unchanged technical inventory, and building the blinded audit
interface. It does not rerun a scientific analysis or alter the locked roster.

## Fixed execution policy

- Authorized parent: `75453ebd1c16268870b6d588e4f54d8668e5e186`.
- The SCC source must be exactly one clean commit above that parent.
- Restoration uses at most two transfers, resumes `.part` files first, and
  stops starting work at 38,700 seconds within an 11:30:00 allocation.
- Restricted progress is updated after each transfer. Aggregate-safe state is
  checkpointed every 25 completions or 15 minutes.
- A2 may issue `LOCKED_ROSTER_SOURCE_RESTORED` or
  `RESTORATION_INCOMPLETE_RESUMABLE`. A3 verifies A2 and either no-ops,
  continues, or issues the bounded final blocker.
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

The submitter runs the full test suite, verifies Job 7354017 accounting and all
pinned hashes, confirms absence of prior completion certificates, and performs
the authoritative disk-state assessment before creating any new output root.
It then submits only the serial A2 to A3 to B1 to B2 dependency chain. If the
assessment finds all files complete, A2 and A3 are skipped and B1 to B2 is
submitted directly.

Do not use a job array, add unrelated dependencies, expose `.netrc`, poll via a
browser automation, or modify prior output roots.
