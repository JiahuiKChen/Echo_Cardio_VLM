# Phase 1I-R8U-R2 Grid Engine log compatibility

Starting authority: `f3df5cd969ff70c87378657767c5bf2b92d4e074`.

This repair gives Grid Engine merged stdout/stderr logs a closed evidence role.
It does not change the scientific file projection or the permission policy for
any scientific, control, claim, capacity, receipt, preservation, or
finalization artifact.

The role requires an exact fixed scheduler root, receipt-derived job name and
numeric job ID, exact array task ID where applicable, a regular nonsymlink
single-link file owned by the effective user on the approved attempt
filesystem, mode `0600` or `0644`, no group/other write or execute bits, a
fixed 16 MiB ceiling, and stable descriptor-bound identity and SHA-256. The
closed roles cover the failed Batch-16 recovery, the fresh R2 recovery, the
future Tasks 17–19 array, and the future held finalizer. Unbound public files
remain blocking.

The consumed R8U-R1 namespace for job `7352656` is read-only historical
evidence. Its exact eight-file inventory, private controls, successful qsub,
application exit 78, zero-effect counters, and terminal `0644` scheduler log
are hash-bound. R2 uses distinct no-clobber roots:

- `r8u_r2_batch16_recovery`
- `r8u_r2_continuation_17_19`

Every fresh R2 artifact binds five implementation epochs: the scientific
commit, R8R implementation, R8U base, fixed projection repair, and current
scheduler-log repair. Capacity remains fixed to one fresh Batch-16 recovery,
Tasks 17–19, finalization, and the existing 200GB reserves.

The recovery submitter performs bounded control checks and one capacity
snapshot before qsub. After the one authorized qsub it performs only a private
receipt readback and one initial qstat snapshot, then returns immediately. It
does not poll and does not submit Tasks 17–19 or the finalizer.

No source selection, extraction algorithm, EchoPrime behavior, pooling,
technical-disposition policy, preservation semantics, model code, prediction
path, or confirmatory analysis path changed.
