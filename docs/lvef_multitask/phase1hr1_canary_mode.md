# Phase 1H-R1/R2 exact-five canary mode

> **Deprecated/noncontrolling.** This document preserves the former
> multi-packet, five-qsub design for comparison. The controlling future route
> is the single-job minimal adapter documented in
> `historical_pipeline_recovery_sprint.md`; none of the live commands below is
> authorized by the recovery sprint.

## Authority boundary

The tracked execution-state authority is
`configs/lvef_c3_execution_state_v1.yaml`. It remains the sole authority for
the governing branch and commit, logical execution attempt, preparation
sequence, next-unused attempt, production-attempt absence, and permitted
execution scopes. R2 embeds the closed canary lifecycle definition in that
same file; it does not create a competing tracked state. A no-body preflight,
private authority preparation, or lifecycle transition does not create or
consume execution attempt 005 or production attempt 006.

One owner-private lifecycle snapshot is derived from and hash-bound to the
exact tracked state bytes. It binds the governing Git commit and canary
`run_id` as separate fields; neither may be inferred from the preparation
sequence identifier or treated as an execution-attempt identity. Snapshot
mutation is expected-current, forward-only, no-clobber, and atomic. It never
edits the tracked YAML.

The canary cohort is a separate owner-private authority. The closed contract in
`scripts/lvef_c3_canary_manifest.py` and
`configs/lvef_c3_canary_manifest_schema_v1.json` requires an exact-five sealed
manifest, while the tracked scheduler template binds only its SHA-256. The
template starts `FROZEN_UNBOUND_UNAUTHORIZED`; binding an owner-private
manifest changes it only to `FROZEN_BOUND_UNAUTHORIZED`. Neither state is an
execution authorization.

The private manifest writer is no-clobber and requires a current-owner mode
`0700` parent and a mode `0600` regular manifest. The manifest binds its source
authority commit, source-manifest SHA-256, named configuration hashes, complete
declared object membership, and a semantic manifest seal. The loader can also
require the SHA-256 of the exact serialized private file.

Future execution has a second closed contract in
`scripts/lvef_c3_canary_execution_authority.py` and
`configs/lvef_c3_canary_execution_authority_schema_v1.json`. Its sole permitted
instance path is
`/restricted/projectnb/mimicecho/lvef_multitask_c3_v2/owner_private/exact_five_canary/execution_authorization_v1.json`.
The canonical serialized packet binds both manifest seals, the exact batch and
scheduler plans, production contract, environment, checkpoint, requester-pays,
gcloud and CRC32C authorities, five stage grants, qsub, and the tracked worker
and launcher. The packet explicitly states that canonical-state execution
permission is also required; neither authority can substitute for the other.

The sole tracked top-level canary entrypoint is
`scripts/scc_run_lvef_c3_canary.sh`. It accepts exactly one argument and only
one of these four modes:

- `--validate-installation` validates tracked state, local/origin Git equality,
  the production release, the frozen scheduler plan and its entrypoint hashes,
  lifecycle and materializer producers, and required production callables.
- `--preflight-only` repeats installation validation and then uses a disposable
  synthetic private root to exercise materialization, both preparation-state
  transitions, packet revalidation, the execute gate, and the five-dispatch
  control path. Its injected submitter records five adapter calls but submits
  zero real qsub jobs. It accesses zero restricted cohort rows and invokes no
  live effect.
- `--prepare-live-authority` is a future owner-authorized metadata preparation
  mode. It validates the SCC private authorities, writes the closed
  identifier-free preselection authority before opening row-bearing inputs,
  materializes and validates the exact-five private artifacts, and advances
  the private lifecycle atomically to `CANARY_MANIFEST_SEALED`. It performs no
  object-body request, qsub, DICOM processing, or GPU work.
- `--execute` is the future live scientific boundary. It requires the private
  lifecycle snapshot already at `CANARY_MANIFEST_SEALED`, with matching
  governing commit and `run_id`, and independently validates the fixed owner
  packet before recording `CANARY_EXECUTING` and reaching the durable
  dispatcher. The tracked base scopes do not, by themselves, permit dispatch.

## R2 lifecycle and private materialization

The tracked lifecycle has exactly these forward edges:

```text
PRECANARY_READY
  -> CANARY_AUTHORITY_PREPARED
  -> CANARY_MANIFEST_SEALED
  -> CANARY_EXECUTING
  -> CANARY_TERMINAL_PASS | CANARY_TERMINAL_FAIL
```

`CANARY_TERMINAL_FAIL` is reachable only from `CANARY_EXECUTING`; a failure
before scientific execution begins leaves the prior nonterminal state
unchanged. The materializer applies the first two edges as one atomic
transaction while retaining both edges and their bindings in snapshot history.
A failed lifecycle transaction leaves the prior snapshot byte-identical;
private publication is no-clobber, so a partial preparation cannot be treated
as permission to retry or continue.

Before reading any restricted row-bearing inventory, the materializer writes
`preselection_scope.restricted.json`, a closed-schema, mode-`0600`, no-clobber
authority receipt. It contains the governing commit, run identity, exact-five
hard ceilings, five-stage scheduler scope, allowed and forbidden operation
classes, timestamp, the exact qsub/qstat path, SHA-256, size, device and inode,
and a semantic seal. The scheduler identities are revalidated before qstat and
again by the execution-packet loader before qsub can be reached. The same
pre-row gate uses the bounded native-quota/pquota/findmnt/df probe to require at
least 10,000,000,000 bytes of current project and physical headroom plus 2,048
project file slots. It contains no subject identifier,
study identifier, source-object key, source path, label, prediction, or
clinical row. The subsequent owner-private materializer may read only the
already-authorized metadata inventories and publishes the selected manifest,
batch and scheduler plans, body-transfer and per-stage grants, and execution
packet inside the fixed private tree. Those row-bearing artifacts and their
paths are never printed or committed.

## Deterministic selection contract

Selection uses source-only inventory facts. Eligible candidates must be in the
`train` split, must not be known no-cine studies, and must not have participated
in the prior reconstruction smoke. For a subject with multiple eligible
studies, the numerically lowest study identifier is the frozen representative.
Representatives are then ordered by expected object count, expected bytes,
numeric subject identifier, and numeric study identifier.

The five ranks use the frozen half-up formula
`(q * (n - 1) + 2) // 4` for `q = 0, 1, 2, 3, 4`, yielding relatively low,
lower-middle, near-median, upper-middle, and relatively high object-count
strata. The result must contain exactly five unique studies from exactly five
unique subjects. There is no substitution if a selected set fails a gate: the
complete cohort must total at most 750 objects and at most 5,000,000,000
expected bytes. Unknown manifest fields and outcome-, label-, prediction-, or
performance-bearing field names fail closed.

## Frozen production-faithful scheduler DAG

`configs/lvef_c3_canary_scheduler_plan_v1.json` freezes exactly five ordered
SGE submissions. Every stage has its own submission, one task, zero stage
retries, and a hash-bound tracked production callable. The canary control plane
holds direct references to the same production implementations for source
transfer and integrity verification, DICOM/extraction, EchoPrime embedding,
temporal sampling and encoder-input preparation, study mean pooling and record
validation, preservation, and retained-cache canary finalization.

| qsub | Stage | Production entrypoint and callable | Dependency and required predecessor receipt | Resource class | Declared outputs | Failure boundary |
|---:|---|---|---|---|---|---|
| 1 | `DOWNLOAD` | `scripts/lvef_c3_orchestration_core.py::execute_exact_batch_download` | none | CPU; `24:00:00`; 16 GB | download resume ledger; verified download manifest; download-verified transition receipt | `BLOCK_ALL_SUCCESSORS_NO_RESUBMISSION` |
| 2 | `DICOM_EXTRACTION` | `scripts/lvef_c3_production_stages.py::run_production_dicom_extraction` | `DOWNLOAD`; download-verified transition receipt | CPU; `48:00:00`; 64 GB | DICOM audit; extraction manifest; extraction-complete transition receipt | `BLOCK_ALL_SUCCESSORS_NO_RESUBMISSION` |
| 3 | `ECHOPRIME_EMBEDDING` | `scripts/lvef_c3_production_stages.py::run_production_echoprime` | `DICOM_EXTRACTION`; extraction-complete transition receipt | GPU; `24:00:00`; 64 GB; one GPU, compute capability at least 8.0 and 48 GB | clip embeddings and manifest; study embeddings and manifest; study-pooling-complete transition receipt | `BLOCK_ALL_SUCCESSORS_NO_RESUBMISSION` |
| 4 | `BATCH_PRESERVATION` | `scripts/preserve_lvef_c3_production_batch.py::preserve_batch` | `ECHOPRIME_EMBEDDING`; study-pooling-complete transition receipt | CPU; `12:00:00`; 32 GB | batch preservation manifest and receipt; cache-retirement-eligible transition receipt | `BLOCK_ALL_SUCCESSORS_NO_RESUBMISSION` |
| 5 | `CANARY_FINALIZATION` | `scripts/finalize_lvef_c3_production.py::finalize_canary_preservation_receipt` | `BATCH_PRESERVATION`; batch preservation receipt | CPU; `12:00:00`; 32 GB | canary finalization receipt; aggregate-safe canary summary | `TERMINAL_CANARY_FAILURE_NO_RESUBMISSION` |

Only `ECHOPRIME_EMBEDDING` requests a GPU. The plan forbids array expansion,
automatic resubmission, and production continuation. The scheduler claim
ledger accepts stages only in the frozen order, requires the prior stage to
have a passing result receipt, rejects duplicate or out-of-order claims, and
caps the submission count at five. Any failed stage is terminal for this
canary. The stage-4 eligibility receipt does not authorize cache retirement;
cache retirement is not a stage in this DAG and is not performed by canary
finalization.

The execute-only dispatcher in `scripts/lvef_c3_canary_dispatch.py` creates a
new mode-`0700` run root and immutable mode-`0600` snapshots before each qsub.
It submits only the five declared commands with scheduler restart disabled
(`-r n`) and an exact predecessor hold chain. A qsub failure is terminal. The
single-stage worker in `scripts/lvef_c3_canary_stage_worker.py` writes an
`O_EXCL` execution claim before any stage effect. Because an SGE job can start
before the dispatcher has published that stage's `SUBMITTED` snapshot, the
worker first performs only a bounded read-only wait for the exact bound
complete five-job `DISPATCHED_FROZEN_DAG` ledger. Timeout or a conflicting
ledger fails closed before the claim or any stage effect. The worker binds its
actual numeric SGE `JOB_ID` to that stage's immutable submission row and a
successor also requires its bound immediate predecessor PASS result. A claimed
or failed stage cannot be retried by this envelope. Every launched worker
additionally requires the private lifecycle at `CANARY_EXECUTING`. The
validated finalization result advances the lifecycle to
`CANARY_TERMINAL_PASS`; the authenticated launcher's failure trap and the
Python worker both use the same state API to advance controlled bootstrap or
stage failures once to `CANARY_TERMINAL_FAIL`. An untrappable node/process loss
cannot satisfy a successor's PASS-receipt gate and therefore remains
scientifically fail-closed, but is not mislabeled as receipt-validated terminal
PASS and requires aggregate-safe operator reconciliation.

“Zero retries” at the scheduler level means zero repeat qsub submissions and
zero whole-stage retries. It does not silently rewrite the production
downloader contract: the shared download callable separately accounts for the
existing bounded, classified per-object transport attempts (currently at most
five per declared object). Those internal transport attempts do not add a
scheduler submission, cannot expand object membership, and do not permit a
failed stage to be resubmitted.

## Phase 1H-R2 current no-live boundary and future sequence

This phase prepares the tracked future control envelope but does not authorize
a real SCC invocation. The tracked manifest module performs deterministic
selection, sealing, and validation without cloud, scheduler, DICOM, GPU,
modeling, prediction, or performance access. The execute-only dispatcher is
reached only after tracked installation, lifecycle, SCC private-authority, and
sealed-packet gates. Each submitted stage launcher independently reloads and
revalidates its packet-sealed launcher/worker hashes, governing commit, and
clean tracked tree before Python imports project modules; the worker then
reloads and validates the packet before running a stage effect. The
top-level installation and preflight modes never reach a real effect path and
sanitize environment variables that could expose cloud credentials or a GPU.

Accordingly, installation validation and synthetic/zero-identifier preflight
may validate tracked hashes, closed schemas, state invariants, authority
binding, DAG order, receipt dependencies, and hard ceilings. They must not read
a real cohort manifest, request object bodies, submit qsub jobs, transfer or
decode DICOM, use a GPU, create embeddings, retire cache, fit a model, generate
predictions, access confirmatory performance, or continue into production.

`--prepare-live-authority` is distinct from those zero-restricted-row modes:
after separate owner authorization it may read restricted metadata and write
owner-private authority artifacts, but it still cannot request object bodies,
submit qsub, decode DICOM, or use a GPU. `--execute` is the sole tracked live
scientific boundary. Neither mode is authorized or run by this R2
documentation task.

After separate owner authorization, and only after the governing commit is
validated equal in the local, origin, and SCC checkouts, the exact future
tracked top-level sequence is:

```console
./scripts/scc_run_lvef_c3_canary.sh --validate-installation
./scripts/scc_run_lvef_c3_canary.sh --preflight-only
./scripts/scc_run_lvef_c3_canary.sh --prepare-live-authority
./scripts/scc_run_lvef_c3_canary.sh --execute
```

Every command must pass before the next begins. Preparation and execution are
one-shot, no-clobber operations and must not be retried. The final lifecycle
state may be recorded only from the validated run receipts: terminal PASS for
the bound aggregate-safe finalization PASS, otherwise terminal FAIL only after
execution began.

Evidence status for this R2 change is intentionally explicit:

- local decisive live-path synthetic acceptance: **PASS (1/1)**;
- local focused control-plane and production-reuse validation: **PASS
  (77/77 and 134/134)**;
- local complete dependency-light suite: **PASS (804/804)**;
- local/origin equality and ending commit: **PENDING**;
- SCC fast-forward and commit equality: **PENDING**;
- SCC `--validate-installation`: **PENDING / NOT RUN**;
- SCC `--preflight-only`: **PENDING / NOT RUN**;
- live authority preparation and exact-five execution: **NOT RUN**.

This document records the locally validated tracked contract. It does not
claim SCC synchronization, SCC installation validation, SCC preflight
success, live owner-private materialization, or canary execution.
