# Phase 1I-R8U-R7C terminal adjudication

R7C is a metadata-only control-plane repair.  It does not rerun a scientific
stage and it does not change the live R8U-R7 worker validator.

## R7B blocker diagnosis

The Task-17 scheduler log was written by the original continuation worker.  Its
call path was:

1. `scc_run_lvef_c3_r8r_recovery_continuation.sh`;
2. `run_r8u_r7_continuation_array_task`;
3. `validate_r8u_r7_continuation_worker_submission`;
4. `_r8u_r7_qstat_projection`; and
5. the inherited `_r8u_r5_qstat_projection`.

The final function maps every rejected live qstat-topology projection to
`SCHEDULER_JOB_ROLE_MISMATCH`.  This was not an independently observed role
string mismatch.  The context builder supplied
`R8U_R7_CONTINUATION_ARRAY` as both its expected and synthetic semantic-role
arguments; no independent observed role was captured, so the retrospective
observed-role value is `NOT_CAPTURED`.  `JOB_ID=7478863` and
`SGE_TASK_ID=17` passed their earlier, distinct gates, establishing observed
job/task values `7478863` and `17`.  The expected full job name was
`lvef_c3_r8u_r7_seq_1be99c64`.  The rejected XML snapshot and its observed
name were not retained, so the observed full job name is `NOT_CAPTURED` and
the exact failed qstat subpredicate cannot be recovered.  Tasks 18 and 19
subsequently passed the same live context validator, excluding a persistent
role or job-name defect.

The validator used `qstat -xml` and the full `JB_name` element.  The visually
truncated interactive `qstat` name column was never authority.  No qacct record
was queried during R7B.  Accordingly, R7B is classified as
`TERMINAL_ADJUDICATION_INFRASTRUCTURE_BLOCKER`, not as proof of a Task-17
scientific failure.  The failing invocation was the original continuation
worker, not a login shell or a control-plane observer.  Therefore no observer
pretended to be a worker; R7B's error was treating an undifferentiated live
topology blocker as terminal scientific evidence.

## R7C boundary

The R7C entrypoint derives completed-job identity from the immutable R7A
continuation receipt and four fixed qacct identities.  It never impersonates a
worker and does not require ambient `JOB_ID`, `JOB_NAME`, `SGE_TASK_ID`,
`NSLOTS`, or `CUDA_VISIBLE_DEVICES` values.  Runtime commit
`1be99c6436293a7cad576e9855ba4cd58a71e156` remains distinct from the R7C
adjudication commit.

Direct SCC execution additionally requires the coordinator's two narrow
preflight attestations, `R8U_R7C_VERIFIED_LOCAL_HEAD` and
`R8U_R7C_VERIFIED_LOCAL_TRACKED_CLEAN`.  They convey the immediately observed
desktop-local Git state; they are not SGE worker variables.  The adjudicator
independently verifies the SCC checkout, origin ref, exact single-commit
descendancy, and tracked cleanliness at entry, then repeats that repository
review immediately before lock publication.  Git is invoked through the fixed
root-owned `/usr/bin/git` with a closed configuration environment.

Every fixed qacct record is paired with its exact merged scheduler log.  PASS
accounting requires the job-kind-specific PASS marker.  Nonzero accounting is
classified as control-plane-only only from an explicit allowlisted marker; an
unknown or bare nonzero exit cannot authorize cohort reconstruction.  Receipt
paths become eligible strictly in Task 17, Batch 17, Task 18, Batch 18, Task
19, Batch 19, and finalizer order.

After all 19 batch chains pass, the original finalizer path
`cohort_finalization/full_c3_finalization.aggregate_safe.json` is checked
first.  A valid, batch-set-bound original receipt is reused.  Only when it is
absent, and finalizer evidence permits Case B or Case C, may the new R7C
metadata-only cohort receipt be published.

All R7C outputs are owner-private, no-follow, no-clobber JSON receipts.  Batch
and cohort review is restricted to immutable plans, JSON receipts, hashes, and
targeted file metadata.  DICOM and NPZ bodies, extraction, EchoPrime,
embeddings, models, predictions, and confirmatory metrics remain outside the
entrypoint's reachable work.
