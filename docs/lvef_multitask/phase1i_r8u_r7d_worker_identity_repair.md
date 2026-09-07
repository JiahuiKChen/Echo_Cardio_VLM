# Phase 1I-R8U-R7D worker-identity repair

R7D is a tail-only control-plane repair for original Tasks 17--19. It does
not change cohort membership, the immutable batch plan, DICOM extraction,
technical-disposition policy, EchoPrime, pooling, preservation, retirement,
or any model or analysis code. Batches 1--16 and the consumed R7A/R7C
evidence remain historical authorities.

## Authority correction

The live worker now establishes controlling identity from the sealed
submission chain, effective UID, numeric scheduler job/task variables, fixed
role, implementation commit, runner and Python hashes, scheduler account,
qsub environment, and the unique task-to-batch assignment before invoking
qstat. These fields remain fail-closed.

The qstat self-check uses full XML and is limited to three observations over
at most 15 seconds. It records only aggregate-safe equality results. A unique
job, task, owner, or full-name contradiction and duplicate self-records block;
temporary absence and non-`r` transitional state do not. Human-width table
names are never authority. The same validator protects the probe, array, and
held finalizer roles.

## Fixed continuation topology

The fresh `r8u_r7d_continuation_17_19` epoch permits exactly one CPU-only
array-context probe at task 17, one Tasks-17--19 GPU array with concurrency
one, and one CPU finalizer held on that array. The probe cannot enter cloud,
DICOM/NPZ body, extraction, GPU, embedding, preservation, or finalization
paths. Process quiescence excludes only the current submitter PID and detects
every other R7D submit, probe, adjudication, array, or finalizer mode.

Capacity is observed once for the remaining three batches, one rolling
extraction cache, final aggregation, preserved control evidence, and any
confirmed partial artifacts. It does not charge Batches 1--16 again. The new
finalizer accepts the sealed Batch-1--16 prefix and only new R7D receipts for
Batches 17--19; consumed R7A failures cannot become scientific success.

## Validation record

The final implementation passed 47 focused R7D proofs and the maintained
dependency-light suite (`1589` passed, `0` failed, `55` fixture-dependent
tests skipped), plus the frozen canary, historical replay, compilation, shell
syntax, export-safety, and structural checks required by this phase.
