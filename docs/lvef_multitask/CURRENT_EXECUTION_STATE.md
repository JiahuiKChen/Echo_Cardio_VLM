# Current execution state

> **Superseded controlling route.** The five-qsub packet/lifecycle design
> described below is retained as historical evidence and is noncontrolling.
> The historical pipeline recovery sprint replaces it with
> `scripts/scc_submit_lvef_c3_minimal_canary.sh`: one sealed manifest, one
> `PREPARED`/`RUNNING`/`PASS`/`FAIL` ledger, and one sequential SCC job. No real
> manifest or live canary is authorized by this repository change. See
> `docs/lvef_multitask/historical_pipeline_recovery_sprint.md`.

The sole tracked execution authority is
`configs/lvef_c3_execution_state_v1.yaml`. Its `current_governing_commit`
policy resolves to the checked-out Git `HEAD`, which must equal origin and the
SCC checkout and descend from the required starting authority. The tracked
file now also defines the canary lifecycle and its policy; lifecycle progress
is recorded only in a hash-bound owner-private snapshot and never by editing
the tracked state.

| Milestone | Current state |
|---|---|
| Logical execution | Attempt 004; executed once; immutable |
| Preserved preparation | Opaque preparation sequence in canonical state; binds logical attempt 004 |
| Next execution | Attempt 005; unused and absent |
| Next production run | Attempt 006; unused and absent |
| Exact-five canary entrypoint | `scripts/scc_run_lvef_c3_canary.sh`; the sole tracked top-level canary entrypoint |
| Exact-five canary lifecycle | Tracked forward-only definition present; initial `PRECANARY_READY`, then `CANARY_AUTHORITY_PREPARED`, `CANARY_MANIFEST_SEALED`, `CANARY_EXECUTING`, and terminal `CANARY_TERMINAL_PASS` or `CANARY_TERMINAL_FAIL` |
| Owner-private lifecycle instance | No live instance or transition is asserted by this document; verification is pending |
| Owner-private authority preparation | Tracked materializer and `--prepare-live-authority` mode present; live preparation has not been run or validated on SCC in this phase |
| Exact-five canary DAG | Five frozen submissions: download, DICOM extraction, EchoPrime embedding, preservation, and canary finalization; only embedding is a GPU stage |
| Exact-five canary execution | Not executed. Dispatch requires a validated owner-private state at `CANARY_MANIFEST_SEALED` plus the separately sealed execution packet; the tracked base policy alone cannot authorize execution |
| Local R2 validation | **PASS — decisive live-path synthetic gate 1/1; focused control-plane 77/77; production identity/reuse 134/134; complete dependency-light suite 804/804** |
| Local/origin equality | **PENDING — no synchronization claimed here** |
| SCC commit equality | **PENDING — no synchronization claimed here** |
| SCC installation validation and preflight | **PENDING / NOT RUN — no SCC pass claimed here** |
| Live authority preparation or canary | **NOT RUN; outside the current no-live implementation boundary** |

The lifecycle separates immutable Git policy from one mutable private run
instance. Governing commit and canary `run_id` are explicit, separate
bindings; neither is an execution-attempt identifier. Every transition names
its expected current state and moves along one declared edge. Preparation uses
one atomic transaction for
`PRECANARY_READY -> CANARY_AUTHORITY_PREPARED -> CANARY_MANIFEST_SEALED`, while
retaining both edges in history. A failed transaction must leave the prior
snapshot byte-identical. A terminal failure is allowed only after the state
has reached `CANARY_EXECUTING`.

The entrypoint accepts exactly one of four modes:

- `--validate-installation` validates tracked state, Git authority, the
  production contract, scheduler plan, hashes, lifecycle/materializer
  producers, and required callables without a live operation.
- `--preflight-only` proves the future preparation and dispatch control path
  in a disposable synthetic private root. It creates and validates a synthetic
  identifier-free preselection authority, sealed manifest, execution packet,
  lifecycle transitions, and five dispatcher-adapter calls. The submitter is
  injected: real qsub submissions, restricted-row reads, cloud requests,
  DICOM processing, and GPU execution remain zero.
- `--prepare-live-authority` is the future owner-authorized preparation mode.
  It first writes a closed, identifier-free preselection authority receipt;
  only then may the owner-private materializer read the already-authorized
  restricted metadata inventories. It validates and no-clobber seals the
  exact-five manifest, batch plan, scheduler plan, stage grants, and execution
  packet, then atomically records both preparation lifecycle edges. Before any
  scheduler probe it also seals the exact qsub/qstat path, SHA-256, size,
  device, and inode and proves current project headroom of at least 10 GB and
  2,048 file slots with the bounded no-body quota probe. It does
  not request object bodies, submit qsub, process DICOM, or use a GPU.
- `--execute` requires the validated private snapshot to be
  `CANARY_MANIFEST_SEALED`, with matching governing commit and run identity,
  and independently revalidates the sealed execution packet. It then records
  `CANARY_EXECUTING` and dispatches exactly the frozen five-job hold chain.
  Every immutable qsub command carries the sealed launcher/worker hashes and
  governing commit. The queued launcher checks its spooled bytes, the stable
  worker bytes, exact Git `HEAD`, and the clean tracked tree before Python can
  import project modules. Each worker then requires the complete five-job
  dispatch, its exact scheduler `JOB_ID`, and the executing lifecycle before
  claiming a stage. Validated finalization records terminal PASS; the queued
  launcher's authenticated failure trap and the Python worker both record
  controlled bootstrap or stage failures as terminal FAIL through the same
  state API. An untrappable node/process loss remains scientifically
  fail-closed because no successor can produce the required bound PASS receipt,
  but requires aggregate-safe operator reconciliation rather than an inferred
  success state.

No real invocation is authorized by this R2 documentation update. After a
separate owner authorization and only after local/origin/SCC equality and SCC
validation are recorded, the exact tracked top-level sequence is:

```console
./scripts/scc_run_lvef_c3_canary.sh --validate-installation
./scripts/scc_run_lvef_c3_canary.sh --preflight-only
./scripts/scc_run_lvef_c3_canary.sh --prepare-live-authority
./scripts/scc_run_lvef_c3_canary.sh --execute
```

Stop on any nonzero result; do not repeat preparation or execution. The first
two commands are no-live validation. The third reads restricted metadata and
creates owner-private authority artifacts but performs no scientific body
operation. The fourth is the sole live scientific boundary and must not be run
under the current phase authorization.

The frozen path directly reuses the production download, integrity,
DICOM/extraction, EchoPrime, temporal sampling, encoder-input, study-pooling,
preservation, and canary-finalization callables. Its scheduler contract allows
exactly five ordered stage submissions, one GPU stage, zero stage retries or
resubmissions, no cache retirement, and no production continuation. The live
envelope uses durable no-clobber dispatch and stage claims; every successor
requires its predecessor's bound PASS result. Commit-specific SCC evidence,
owner-private identifiers, manifest bytes, packet bytes, and private paths
remain outside Git and must not be inferred from this document.
