# R7H held-publication successor

The authentication successor produced at
`08a2c05a7a850acc40ca169a2a50b28bbf3594b9` exposed a control-publication race.
Array Task 17 started before its complete submission authority was durable and
stopped after its bounded wait with `R7HA_SUBMISSION_RECEIPT_TIMEOUT`.
Scheduler accounting recorded exit status 78 after 61 seconds. The remaining
array tasks and dependent finalizer were held and cancelled. No successor
download journal, credential checkpoint, tail payload, extraction output, or
tail final receipt was produced.

The fixed publication-failure manifest is bound by SHA-256
`3747c4366d9f3d5cceafb1e1a201650e986e543523a6b05be7dad8c9eca7a86e`.
Its closed contract binds the original v1 controls, terminal and cancellation
evidence, scheduler/process quiescence, the unchanged original authentication
failure, and all 16 finalized-prefix receipts. Any v1 worker receipts must be
listed and retained; their absence is not assumed from the job role. Task 17's
worker and all three immediate credential and download-journal checkpoints
must remain absent. The v1 control publications are never reused as live v2
authority or overwritten.

## Fixed scope and source authority

`lvef_c3_r8u_r7h_auth_publication_successor.py` owns only the fixed
`r7h_auth_successor_v2` execution-control namespace. It accepts a clean,
published, linear corrective descendant of the v1 producer, with changes
restricted to the listed runtime/control files and their tests and maintained
documentation. The scientific attempt, plan, source membership, Tasks 17–19,
530 remaining studies, original terminal authentication journal, and finalized
prefix remain unchanged.

The core has a distinct v2 download-authority type that additionally consumes
the exact publication-failure manifest. It checks the original v1 files on
every execution and refuses any v1 nested download control directory. A fresh
v2 journal requires empty canonical tail payload roots. Later v2 tasks and
resume may retain only their own bound execution's growing payload. Canonical
payload paths and the recursive preservation/retirement inventory retain
their established contracts. Authentication remains nonretryable.

## Publish before release

V2 obtains fresh capacity and a fresh CPU credential probe. Both the probe and
the scientific array use `qsub -clear -h`, so the user hold is installed by the
scheduler before a worker can start. The scientific array retains Tasks 17–19,
maximum concurrency one, and the existing resource limits. Its CPU finalizer
is submitted with `-hold_jid` naming that exact array.

The controller publishes and reopens every required submission receipt,
download binding, and combined authority graph before releasing the array.
It checks the exact held scheduler task scope, seals a separate release claim,
and invokes only `qrls -h u` for that one bound job. The probe follows the same
sequence using its smaller authority graph.

The observed SGE `qrls` link must point exactly to its sibling `qalter`, whose
resolved regular, root-owned executable is pinned by SHA-256
`1f2b544bcb31b82d88f453a47f9570ad34ac068871e3fc9164c1dfb8fe8faf12`.
The invocation retains the `qrls` basename. Its authority, exact arguments,
environment, and bounded raw command results are bound into private controls.
An uncertain release result preserves the exclusive release claim and cannot
be retried through submission. Workers require the durable release claim and
complete graph, and can start before the post-release receipt is written.

A JSON publication may recover from a readback exception only after its own
atomic publisher returned the expected digest and an independent private
reader verifies the exact expected bytes. Every preexisting destination and
every publisher exception still fails without adopting output.

## Completed probe logs and finalization

The SGE stdout producer may create the completed probe log with mode 0644.
V2 accepts only that exact successful, identity-bound probe stdout under its
owner-private scheduler directory, with a stable nofollow descriptor, one
regular owner file, mode 0600 or 0644, and a 64 KiB bound. It changes no bytes or
permissions. JSON authority receipts retain their strict mode-0600 contract.

The finalizer selects the v1 or v2 validator only through its exact fixed
binding path. V2 additionally binds the release claim and new worker job
identities. It retains the existing four scientific implementation epochs,
all 19 required batch receipts, serialization/preservation replay, no-clobber
cohort outputs, and `PASS_PRODUCTION_C3_FINALIZED` closure. No fitting,
endpoint prediction, new technical dispositions, or confirmatory-performance
access is authorized.

Focused regressions cover exact publication recovery, empty scheduler
captures, held submission and single release, worker startup during release,
altered consumed evidence, foreign execution bindings, unexpected payload,
and the completed probe's restricted log contract. The maintained suite and
applicable syntax/export checks run after substantive shared changes.
