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
| Phase 1E-G local gates | State invariants, component suite, and end-to-end capture fixture required to pass |
| SCC completion evidence | Current tracked-entrypoint output plus the owner-private no-clobber receipt for the same Git `HEAD` |
| DICOM canary | Not authorized; awaits one separate bounded owner authorization |

Only `--preflight-only` and `--capture-current-environment` are permitted in
this phase. Commit-specific SCC receipt bytes and hashes remain in restricted
authority and are reported in the terminal handoff rather than duplicated in
Git.
