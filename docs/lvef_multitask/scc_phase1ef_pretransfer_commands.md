# Phase 1E-F SCC pretransfer lock commands

Status: **offline only; no cloud, scheduler, DICOM, or model execution**.

The only supported Phase 1E-F attempt entrypoint is the tracked executable:

`scripts/scc_execute_lvef_c3_phase1ef_attempt.sh`

The dispatcher contains the complete offline workflow. Critical execution no
longer extracts or evaluates a Bash fence from this Markdown document.

## Canonical preexecution authority

The owner-private mode-600 environment is created only by
`scripts/scc_prepare_lvef_c3_phase1ef_environment.sh`. Its path is supplied to
the dispatcher through `PHASE1EF_ENV`; neither that path nor any environment
value is printed. The dispatcher treats the file as literal `NAME=value` data,
never as shell code.

Every fresh environment is bound to one owner-private mode-600 JSON manifest
with schema name `lvef_c3_phase1ef_preexecution_authority_manifest`, schema
version 1, and zero execution scopes. The manifest contains exactly these
current-commit authority roles:

- `capacity_parser`;
- `capacity_wrapper`;
- `environment_preparer`;
- `phase1ef_runbook`;
- `safe_export_policy`;
- `backup_recovery_policy`;
- `tracked_attempt_dispatcher`.

Before any output path is created, the dispatcher verifies its own canonical
executed bytes, Git branch/commit/origin/ancestry/cleanliness, the private
environment grammar and exact field set, the manifest path/hash/schema, all
seven authority paths/sizes/hashes/modes/owners, exact attempt 004, zero
scopes, and the complete output-collision set. A manifest, environment,
dispatcher, or collision failure is a **preexecution failure**, not an attempt
execution.

Attempts 001, 002, and 003 are immutable and cannot be selected. Attempt 004
may execute only after a separate owner authorization names the synchronized
commit and fresh environment/manifest authority.

## R2 validation command

This nonmutating mode is the only dispatcher mode authorized during Phase
1E-F-R2. The operator sets `PHASE1EF_ENV` to the fresh owner-private file
without echoing it, then runs:

```bash
: "${PHASE1EF_ENV:?set the owner-private Phase 1E-F environment path}"
PHASE1EF_ENV="$PHASE1EF_ENV" \
  /restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask/scripts/scc_execute_lvef_c3_phase1ef_attempt.sh --preflight-only
```

`--preflight-only` stops after every authority and collision check and before
the first `mkdir`. It cannot invoke capacity capture, backup/restore, packet or
launch-envelope creation, cloud access, or scheduler submission.

## Future single-attempt command

The execution form is preserved here only as an **UNEXECUTED** interface. It
must not be run without a new, explicit owner authorization:

```bash
: "${PHASE1EF_ENV:?set the owner-private Phase 1E-F environment path}"
PHASE1EF_ENV="$PHASE1EF_ENV" \
  /restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask/scripts/scc_execute_lvef_c3_phase1ef_attempt.sh --execute
```

The offline workflow remains zero-scope: it does not request Google Cloud,
list or download objects, submit `qsub`, decode a real DICOM, run EchoPrime,
create embeddings, fit models, generate predictions, or access confirmatory
performance. Its generated first-batch command remains separately gated and
unexecuted.
