# Phase 1I R8U-R7G: R7F terminal adjudication

## Purpose

R7G is an additive, retrospective control-plane layer for the already
completed R7F array `7480830` (Tasks 17--19) and finalizer `7480831`. It may
read only their fixed accounting, role-bound logs, and sealed metadata. It has
no scientific worker, retry, resubmission, extraction, embedding, modeling,
prediction, or performance-access path.

The preceding R7FT result is
`R7FT_TERMINAL_ADJUDICATION_TOOLING_INCOMPATIBILITY`, not a task or cohort
failure. The historical R7C adjudicator requires its HEAD to be exactly one
child of runtime `1be99c6436293a7cad576e9855ba4cd58a71e156`, while R7F ran at
`2223d9768a1cc23efbe95a3c5474ea747a383a10`. Its accounting scope is likewise
closed over old jobs `7478863`/`7478864` and rejects other specifications with
`R8U_R7C_ACCOUNTING_SCOPE_INVALID`. R7G leaves that historical authority
unchanged.

## Fixed authority and outputs

The R7G terminal authority is derived from the hash-pinned R7F capacity,
continuation-claim, array-submission, finalizer-submission, and combined
submission receipts. It cross-binds the scheduler account and CPU-probe
receipts, then permits exactly these qacct records:

- array `7480830`, Task 17;
- array `7480830`, Task 18;
- array `7480830`, Task 19;
- finalizer `7480831`.

The completed event stays bound to R7F runtime commit `2223d976...`; every new
terminal receipt is separately bound to the direct-child R7G adjudication
commit. Outputs live only under
`r8u_r7g_r7f_terminal_adjudication/`, use owner-private directories and files,
and are atomically published without following links or clobbering an existing
different receipt.

The closed entrypoint is:

```text
python3 scripts/lvef_c3_r8u_r7g_terminal_adjudicator.py \
  --adjudicate-fixed-r8u-r7f-existing-jobs
```

It reuses a valid fixed accounting receipt or performs one bounded qacct read
when absent. Each tail task is then independently reconciled to its own log,
stage receipts, preservation transition, cache retirement, final ledger, and
batch-finalization receipt. Only after all three pass does it adjudicate the
finalizer, validate all 19 ordered batches and the exact cohort aggregates,
publish or reuse the R7G cohort receipt, prove quiescence and repository
equality, and publish or reuse the post-reconstruction lock.
