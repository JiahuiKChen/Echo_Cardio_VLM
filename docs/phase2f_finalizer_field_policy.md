# Phase 2F finalizer field policy

`PHASE2F_FINALIZER_FIELD_POLICY_V1` separates fixed protocol identity from
outputs of analyses that were rerun after the approved acquisition-metadata
correction. The policy is intentionally limited to the five non-image result
tables and the comparison-packet provenance schema consumed by the Phase 2F
finalizer.

Protocol, cohort, row-set, source, split, grid, and observed-label invariants
remain exact. For an `INPUT_CHANGED` analysis, validation-selected alpha,
selection flags, performance metrics, and confusion-matrix cells may change
only after the frozen grid, validation-only selection rule, deterministic
tie-breaking, cohort counts, and observed-positive/negative counts pass.
For an `INPUT_UNCHANGED` analysis, the same fields must reconcile under the
existing reuse policy. Boolean values are never subtracted.

The preflight checks the exact schema of every current table and every leaf
field in the nine comparison provenance packets. Any unknown field, changed
protocol invariant, invalid alpha selection, changed prevalence, or changed
source hash fails before scheduler submission.
