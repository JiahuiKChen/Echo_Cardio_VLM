# Current execution state

The sole Phase 1E-G identity authority is
`configs/lvef_c3_execution_state_v1.yaml`. Its `current_governing_commit`
policy resolves to the checked-out Git `HEAD`, which must equal origin and the
SCC checkout and descend from the required starting authority.

| Milestone | Current state |
|---|---|
| Logical execution | Attempt 004; executed once; immutable |
| Preserved preparation | Opaque preparation sequence in canonical state; binds logical attempt 004 |
| Next execution | Attempt 005; unused and absent |
| Next production run | Attempt 006; unused and absent |
| Exact-five canary entrypoint | `scripts/scc_run_lvef_c3_canary.sh`; the sole tracked top-level canary entrypoint |
| Exact-five canary DAG | Five frozen submissions: download, DICOM extraction, EchoPrime embedding, preservation, and canary finalization; only embedding is a GPU stage |
| Exact-five canary execution | Not authorized and not executed; canonical state lacks `execute_exact_five_canary`, and no sealed owner-private execution packet or manifest exists |
| SCC Phase 1H-R1 evidence | No live installation-validation or preflight success is claimed in this tracked document |

The entrypoint accepts exactly one of three modes:

- `--validate-installation` validates the tracked state, Git authority,
  production contract, scheduler plan, hashes, and required callables without
  live operations.
- `--preflight-only` additionally builds and validates the synthetic exact-five
  manifest, bound scheduler plan, and production immutable batch plan. It reads
  no restricted cohort row and performs no cloud request, qsub submission,
  DICOM processing, or GPU execution.
- `--execute` first requires the future canonical-state scope
  `execute_exact_five_canary`. The current state denies that scope, so it fails
  before private discovery, run-root creation, or qsub with
  `REAL_CANARY_CANONICAL_STATE_AUTHORIZATION_REQUIRED`. A later state
  transition would still be insufficient without the separately sealed
  owner-private packet defined by
  `configs/lvef_c3_canary_execution_authority_schema_v1.json`.

The frozen exact-five path directly reuses the production download, download
verification, DICOM/extraction, EchoPrime, temporal sampling, encoder-input,
study-pooling, preservation, and canary-finalization callables. Its scheduler
contract allows exactly five ordered stage submissions, one GPU stage, zero
stage retries or resubmissions, no cache retirement, and no production
continuation. The execute-only envelope uses durable no-clobber dispatch and
stage claims; every successor requires its predecessor's bound PASS result.
Commit-specific SCC evidence and owner-private manifest bytes remain outside
Git and must not be inferred from this document.
