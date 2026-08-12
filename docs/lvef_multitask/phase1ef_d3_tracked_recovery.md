# Phase 1E-G tracked D3 recovery authority

Status: **implemented for local validation; SCC preflight and capture remain
unreported here**.

## Authority repair

The preparation record identifier
`lvef_multitask_phase1ef_r2_attempt004_preparation_6a814b3_attempt_005`
belongs to the preserved preparation sequence for logical execution attempt
004. Its final token does not turn it into logical attempt 005. The former
validator conflated those namespaces and therefore rejected the valid binding
with `PREPARATION_BINDING_INVALID`.

`configs/lvef_c3_execution_state_v1.yaml` now governs the logical attempt,
preparation sequence, next unused execution attempt, production-attempt
namespace, execution count, governing Git authority, and permitted scopes.
Preparers, manifests, validators, dispatchers, and reports must derive those
values from the state file and may not infer one namespace from another.

## Operative tracked interface

The sole Phase 1E-G SCC entrypoint is
`scripts/scc_finalize_lvef_phase1ef_d3.sh`, backed by
`scripts/finalize_lvef_phase1ef_d3.py`. It has exactly these modes:

```bash
scripts/scc_finalize_lvef_phase1ef_d3.sh --preflight-only
scripts/scc_finalize_lvef_phase1ef_d3.sh --capture-current-environment
```

There is no expected-commit positional argument. The entrypoint reads the
canonical state and current Git checkout directly. SCC Git fast-forward is a
separate operator step documented in `scc_phase1ef_pretransfer_commands.md`.

The preflight performs state load, preparation discovery and binding,
private-authority resolution, Git/branch/origin checks, immutable capacity
validation, absence checks, and receipt validation without writing a
scientific artifact or creating an attempt. The capture mode permits only one
no-clobber offline current-environment receipt and validates it before success.

## Preserved historical artifact

`scripts/scc_finalize_lvef_phase1ef_d3_canonical_2088832.sh` is sealed exact-byte
historical evidence. It is **nonoperative**: its pinned commit and positional
interface must not be used for Phase 1E-G. The associated closed mapping remains
`phase1ef_d3_canonical_operation_mapping.json`.

## Closed scope

Both modes preserve attempt 004 and its two sealed capacity artifacts, require
attempt 005 and production attempt 006 to remain absent, and provide no route
to cloud requests, scheduler submission, DICOM access or extraction, GPU or
EchoPrime inference, embeddings, modeling, predictions, or confirmatory
performance. No SCC success is asserted by this document; the live result must
come from the validated SCC receipt and synchronized Git state.
