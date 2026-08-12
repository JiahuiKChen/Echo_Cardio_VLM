# Phase 1H-R1 exact-five canary mode

## Authority boundary

The tracked execution-state authority is
`configs/lvef_c3_execution_state_v1.yaml`. It remains the sole authority for
the governing branch and commit, logical execution attempt, preparation
sequence, next-unused attempt, production-attempt absence, and permitted
execution scopes. A no-body preflight does not create or consume an execution
attempt.

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
one of these three modes:

- `--validate-installation` validates tracked state, local/origin Git equality,
  the production release, the frozen scheduler plan and its entrypoint hashes,
  and required production callables.
- `--preflight-only` repeats installation validation and then validates a
  synthetic exact-five manifest, its bound scheduler plan, and the production
  immutable batch-plan projection. It accesses zero restricted cohort rows and
  invokes no live effect.
- `--execute` is a future-operable, double-gated mode. It first requires the
  absent canonical-state scope `execute_exact_five_canary`, then validates the
  SCC private authorities and fixed owner packet before reaching the durable
  dispatcher. The current driver therefore fails at the first gate with
  `REAL_CANARY_CANONICAL_STATE_AUTHORIZATION_REQUIRED`; it has not been invoked
  for a canary.

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
`O_EXCL` execution claim before any stage effect and requires a bound
predecessor PASS result before a successor can act. A claimed or failed stage
cannot be retried by this envelope.

“Zero retries” at the scheduler level means zero repeat qsub submissions and
zero whole-stage retries. It does not silently rewrite the production
downloader contract: the shared download callable separately accounts for the
existing bounded, classified per-object transport attempts (currently at most
five per declared object). Those internal transport attempts do not add a
scheduler submission, cannot expand object membership, and do not permit a
failed stage to be resubmitted.

## Phase 1H-R1 no-body boundary

This phase implements and validates the future execution envelope but does not
authorize it. The tracked manifest module performs deterministic selection,
sealing, and validation without cloud, scheduler, DICOM, GPU, modeling,
prediction, or performance access. The execute-only dispatcher is imported
only after the absent canonical scope, tracked installation, and SCC private-
authority gates; it receives the fully validated owner packet before dispatch.
Each submitted stage launcher independently reloads and validates that packet
before importing or running a stage effect. The top-level installation and
preflight modes never reach these paths and sanitize environment variables that
could expose cloud credentials or a GPU.

Accordingly, installation validation and synthetic/zero-identifier preflight
may validate tracked hashes, closed schemas, state invariants, authority
binding, DAG order, receipt dependencies, and hard ceilings. They must not read
a real cohort manifest, request object bodies, submit qsub jobs, transfer or
decode DICOM, use a GPU, create embeddings, retire cache, fit a model, generate
predictions, access confirmatory performance, or continue into production.
This document records the implemented design; it does not claim a live SCC
installation validation, preflight, or canary execution succeeded.
