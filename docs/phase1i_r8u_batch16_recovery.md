# Phase 1I-R8U fixed Batch-16 recovery

Phase 1I-R8U adds one destination-specific recovery epoch to the sealed
`lvef_c3_full_904d0ab65f003c1e_e1cdb674` attempt. It does not expose a
general retry interface and does not change cohort selection, source-object
membership, extraction, EchoPrime, pooling, technical-disposition, modeling,
prediction, or analysis behavior.

The fixed sequence is:

1. Revalidate the exact Batch-1--15 final-receipt authorities, historical R8R
   chain, retained Batch-16 raw controls, failed-partial metadata seal, whole
   attempt/R4 projections, scheduler/process quiescence, and the sole live
   capacity observation.
2. Submit one non-array GPU job for original Task 16. It rehashes all 18,677
   retained raw DICOM bodies, performs no cloud request or download, extracts
   into a new fixed cache, atomically publishes it with no-replace semantics,
   runs EchoPrime, preserves the batch, retires fresh clips, and finalizes the
   Batch-16 receipt. The 4,757 failed partial NPZs remain untouched and are
   never candidates for adoption.
3. After aggregate-safe terminal validation and `qacct failed=0,
   exit_status=0`, submit one `17-19` GPU array with `-tc 1` and one CPU
   finalizer held on that array. Initial qstat must prove the exact task range,
   Tasks 18--19 queued, and the finalizer held before the submission receipt
   authorizes workers to cross their pre-body gate.
4. The cohort finalizer requires exactly three implementation epochs:
   original Batches 1--2, the immutable R8R epoch for Batches 3--15, and the
   R8U epoch for Batches 16--19. It closes all R8R/R8U chain artifacts and all
   19 batch receipts while keeping modeling and confirmatory paths unreachable.

The production entry points are fixed flags on
`scripts/lvef_c3_r8r_recovery_continuation.py`; none accepts an attempt, plan,
batch, task range, cache path, or retry count. The maximum new scheduler
submissions is three, automatic retry is disabled, and capacity is captured
exactly once before the first qsub.

Focused dependency-light coverage is split across:

- `tests/test_lvef_c3_r8u_capacity.py`
- `tests/test_lvef_c3_r8u_batch16_recovery.py`
- `tests/test_lvef_c3_r8u_runner.py`
- `tests/test_lvef_c3_r8u_finalizer.py`

Together these cover the twenty owner-required recovery, immutability,
authority, continuation, finalizer, and submission-ceiling proofs.
