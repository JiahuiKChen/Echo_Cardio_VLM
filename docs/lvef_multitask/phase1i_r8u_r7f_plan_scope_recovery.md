# Phase 1I-R8U-R7F immutable-plan projection recovery

R7F is an additive control-plane repair for the original Tasks 17--19. It
preserves the immutable plan, the 16 finalized batch receipts, the consumed
R7E failure namespace, and all scientific execution semantics.

## R7E mismatch diagnosis

The immutable plan remains valid at SHA-256
`904d0ab65f003c1eb68adeee8c0b1dd786ec7a9ef4bb496b646b22cc7a540247`.
It contains 19 ordered batches, 4,530 studies, 335,984 source objects, and
1,216,569,133,322 source bytes under scientific commit
`e1cdb674ada23bbc9f3a1ff77c33927bd324d3ed`.

The R7E focused fixture used an aggregate-equivalent synthetic tail. Its
object distribution was 15,000 / 15,000 / 9,607 and its byte distribution was
60,000,000,000 / 55,000,000,000 / 28,890,036,746. The real immutable-plan
tail is:

| Task | Batch | Studies | Objects | Source bytes |
| ---: | --- | ---: | ---: | ---: |
| 17 | `c3_batch_016` | 250 | 18,606 | 66,807,894,336 |
| 18 | `c3_batch_017` | 250 | 18,658 | 68,754,613,138 |
| 19 | `c3_batch_018` | 30 | 2,343 | 9,640,479,152 |

The study and object totals match, but the production tail contains
145,202,986,626 source bytes. Its largest batch contains 18,658 objects and
68,754,613,138 source bytes. With the unchanged coefficients and reserves,
the one-active-cache demand is 89,873,645,568 bytes, total incremental demand
is 380,995,646,484 bytes, and required file slots are 158,265.

The exact synthetic-fixture-dependent mismatches are therefore:

- `remaining_source_bytes`;
- `largest_remaining_batch_objects`;
- `largest_remaining_batch_source_bytes`;
- `active_extraction_cache_demand_bytes`;
- `incremental_demand_bytes`;
- `required_file_slots`.

## Additive repair contract

R7F derives the Tasks-17--19 scalar projection from the hash-validated
immutable plan before any live command. Static mismatch receipts identify the
exact scalar and consume zero live observations. A distinct owner-private,
no-clobber R7F namespace preserves the R7E failure receipt and binds the
current direct-child implementation epoch.

Only a valid static projection can reach the fixed live registry: one
`pquota`, two `findmnt`, two `df`, and zero `du` commands. A sealed capacity
PASS then authorizes one CPU context probe, one Tasks-17--19 GPU array with
concurrency one, and one CPU finalizer held on that array. No fourth qsub is
reachable.
