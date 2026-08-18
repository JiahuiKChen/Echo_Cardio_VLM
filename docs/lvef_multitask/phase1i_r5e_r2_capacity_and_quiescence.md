# Phase 1I R5E-R2 implementation record

R5E-R2 adds one append-only dynamic-capacity evidence role,
`R5E_R2_PRE_ACTION`, with a fixed owner-private no-clobber receipt/summary
pair. The occupied R5E pre-cleanup pair remains historical and is not a
current-commit claim authority. A fresh successor may use the R5E-R2 pair
only when it is current and passing, or the existing post-cleanup pair after
an exact successful older-raw retirement.

The retirement controller now evaluates quiescence against the two fixed raw
object leaves. It takes one scheduler snapshot and one same-user process
snapshot, inspects procfs only for candidate or unknown process scope, and
requires two equal metadata-only leaf inventories. A clear unrelated command
does not become blocking merely because optional procfs metadata is
inaccessible. Candidate or unknown inaccessible authority, target references,
matching jobs, unexpected leaf entries, or snapshot drift remain closed
blockers.

The target scope, raw counts and bytes, retained evidence, R4 authority,
capacity arithmetic, scientific pipeline, and two-submission scheduler
topology are unchanged. Neither DICOM nor NPZ bodies are opened by the new
quiescence proof.
