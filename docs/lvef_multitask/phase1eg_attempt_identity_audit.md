# Phase 1E-G attempt-identity audit

Status: **closed-schema authority adopted; historical records preserved**.

The tracked search covered material `004`, `005`, `006`, preparation, and
next-attempt representations in `configs/`, `scripts/`, `tests/`, and
`docs/lvef_multitask/`. The only live identity authority is
`configs/lvef_c3_execution_state_v1.yaml`. A numeric token is interpreted only
with its namespace and field role; suffix parsing is forbidden.

## Logical execution attempt

- The canonical state defines logical attempt 004, its governing commit, and
  `attempt_004_execution_count: 1`.
- `configs/lvef_multitask_revalidation.yaml` retains attempts 001–004 as an
  immutable historical ledger. Its live next-attempt value is no longer
  duplicated; it points to the canonical state.
- The operative loader/finalizer/entrypoint group is
  `scripts/lvef_c3_execution_state.py`,
  `scripts/finalize_lvef_phase1ef_d3.py`, and
  `scripts/scc_finalize_lvef_phase1ef_d3.sh`. The preparation, manifest, and
  dispatcher group is `scripts/scc_prepare_lvef_c3_phase1ef_environment.sh`,
  `scripts/lvef_c3_phase1ef_authority_manifest.py`,
  `scripts/scc_capture_lvef_c3_post_reallocation_capacity.sh`, and
  `scripts/scc_execute_lvef_c3_phase1ef_attempt.sh`. Logical identity in these
  paths must be loaded or rendered from canonical state, never recovered from a
  preparation name.
- The corresponding test fixtures in
  `test_lvef_c3_execution_state.py`,
  `test_lvef_c3_phase1ef_d3_recovery.py`,
  `test_lvef_c3_phase1ef_dispatcher.py`,
  `test_lvef_c3_post_reallocation_capacity.py`, and
  `test_scc_prepare_lvef_c3_phase1ef_environment.py` represent logical-attempt
  history, rejection, or no-clobber checks; they are not independent runtime
  authorities.

## Preparation sequence

- The canonical preparation sequence is
  `lvef_multitask_phase1ef_r2_attempt004_preparation_6a814b3_attempt_005`, with
  the canonical environment filename and byte count recorded beside it. The
  terminal `_attempt_005` is a preparation-sequence token only.
- Preserved preparation discovery and binding occur only in
  `scripts/finalize_lvef_phase1ef_d3.py`. Its names use
  `preparation_sequence_*` or `preparation_*`, not `expected_attempt`. The
  separate prospective preparer and manifest derive a next-unused execution
  attempt; they do not reinterpret this preserved sequence.
- `phase1ef_attempt004_authority.env`, the archived environment names in
  `scripts/archive_lvef_c3_phase1ef_environment.py`, and preparation/manifest
  filenames containing `attempt004` or `attempt005` are preserved artifact
  names. They do not establish an execution attempt.
- Preparation fixtures in `test_lvef_c3_phase1ef_d3_recovery.py`,
  `test_lvef_c3_phase1ef_dispatcher.py`, and
  `test_scc_prepare_lvef_c3_phase1ef_environment.py` exercise this binding and
  are classified as test representations of preparation sequence identity.

## Next unused execution attempt

- `next_unused_execution_attempt: 5` and `attempt_005_exists: false` in the
  canonical state mean that logical attempt 005 is available only as an absent
  collision target. They neither create nor authorize it.
- Absence checks in the finalizer, preparer, dispatcher, capacity wrapper, and
  their tests represent this next-unused collision guard. They must derive the
  formatted ID from state and may not treat the preserved preparation as proof
  that attempt 005 exists.
- `configs/lvef_multitask_revalidation.yaml` now references the canonical
  `next_unused_execution_attempt` field and retains
  `next_attempt_authorized: false` without repeating an ID.

## Production run identifier

- The separate production namespace in canonical state records historical
  production attempt 005 as the prior offline authority packet and production
  attempt 006 as next unused and absent. It is unrelated to the Phase 1E-F
  logical-attempt namespace.
- Historical production attempts 001–005 in
  `configs/lvef_multitask_revalidation.yaml` remain immutable records.
  `lvef_c3_phase1ee_production_lock_006` occurrences in the finalizer,
  preparer/dispatcher, finalization and terminal-seal tests, and D3 fixtures
  are production collision targets only; they do not assert existence or grant
  execution authority.

## Historical prose or sealed evidence only

- `scripts/scc_finalize_lvef_phase1ef_d3_canonical_2088832.sh` is sealed
  exact-byte historical evidence and is explicitly **nonoperative**. Its pinned
  attempt literals and positional interface are not exceptions for new code.
- Phase 1E-D attempt 004 in `scc_phase1ed_pretransfer_commands.md`,
  `phase1ed_pretransfer_lock_findings.md`, its config record, and
  `test_scc_capture_lvef_c3_live_quota.py` belongs to the distinct Phase 1E-D
  specification-lock namespace.
- Historical narrative in `README.md`,
  `phase1e_full_c3_pre_authorization.md`,
  `phase1e_next_pretransfer_lock.md`,
  `phase1ef_post_reallocation_lock.md`, and
  `phase1ef_d3_environment_capture_diagnosis.md` records earlier dispositions
  only. The two current operator documents are
  `phase1ef_d3_tracked_recovery.md` and
  `scc_phase1ef_pretransfer_commands.md`, both subordinate to canonical state.
- `phase1ef_d3_canonical_operation_mapping.json` describes the sealed
  predecessor and its negative-scope guarantees; it is historical mapping, not
  an execution-state source.

## Rejection rule

New operative code must use explicit names for
`logical_execution_attempt`, `preparation_sequence_id`,
`next_unused_execution_attempt`, and `next_unused_production_attempt`. It must
reject an unknown state key, a noncanonical invariant, or any comparison that
binds a preparation identifier to a logical-attempt ID. Hardcoded live 004/005
or production 006 IDs are permitted only in the canonical state or the sealed
nonoperative artifact; tests may contain deliberate negative fixtures.
