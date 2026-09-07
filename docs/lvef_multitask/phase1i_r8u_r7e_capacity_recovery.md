# Phase 1I-R8U-R7E field-level capacity recovery

R7E is an additive, tail-only control-plane repair for the original Tasks
17--19. It preserves the consumed R7D observation and Batches 1--16. It does
not change cohort selection, the fixed batch plan, extraction, EchoPrime,
technical disposition, preservation, cache retirement, or scientific
finalization semantics.

## Consumed R7D diagnosis

The consumed invocation emitted only the outer status
`BLOCKED_R8U_R7D_CAPACITY_INVALID`. The controller caught every capacity
exception as `R8U_R7D_CAPACITY_INVALID`, after which the guarded CLI added the
`BLOCKED_` prefix. No inner status, failed predicate, command record, parser
result, raw-capture digest, or numerical margin was persisted. The defensible
classification is therefore `DIAGNOSTIC_DETAIL_NOT_PERSISTED`, not a measured
capacity deficit.

## Additive repair

The new `r8u_r7e_capacity_recovery` namespace authorizes one observation and
seals a mode-0600 no-clobber receipt. The observation executes the fixed
registry exactly once: one `pquota`, two `findmnt`, two `df`, and zero `du`
commands. Private raw captures remain outside aggregate output. Pure replay
derives command counts, parser and mount/quota bindings, filesystem arithmetic,
remaining-scope demand, and quota, physical-byte, and file-slot margins. A
valid measured deficit is distinct from a field-specific observation failure.

A sealed capacity PASS can be resumed without recapture. Only then is the R7E
continuation claim created. That claim binds the existing CPU worker-context
probe path and, after an exact probe PASS, one Tasks-17--19 GPU array with
concurrency one and one CPU finalizer held on that array. Array, finalizer, and
combined submission receipts are distinct and no fourth submission is
reachable. The implementation epoch is constrained to the sole child of the
fixed R7D commit.

## Validation

Focused dependency-light proofs cover passing and deficit observations,
command and parser failures, missing and nonregular captures, counters,
ambiguous mounts, quota authority, numeric and filesystem arithmetic,
field-specific controller propagation, receipt privacy and no-clobber
publication, sealed-PASS resumption, all three CLI dispatches, and the exact
array/finalizer topology. Python compilation, changed-file review, strict
fixture checks, `git diff --check`, and the maintained dependency-light suite
are required before the one authorized live SCC observation.
