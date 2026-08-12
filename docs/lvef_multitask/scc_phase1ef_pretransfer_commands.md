# Phase 1E-G SCC pre-canary commands

Status: **tracked interface ready; SCC preflight and capture not yet reported**.

## Execution authority

`configs/lvef_c3_execution_state_v1.yaml` is the single execution-state
authority. It keeps these identities separate:

- logical execution attempt 004, executed exactly once and immutable;
- the preserved preparation sequence
  `lvef_multitask_phase1ef_r2_attempt004_preparation_6a814b3_attempt_005`;
- next unused logical execution attempt 005, which does not exist; and
- next unused production attempt 006, which does not exist.

The suffix `_attempt_005` in the preparation sequence identifier is not a
logical execution-attempt claim. The preparation, validator, and D3 entrypoint
must load the canonical state rather than infer identity from that suffix.

## Separate Git fast-forward

Fast-forward the clean SCC worktree before invoking D3. Git synchronization is
not an entrypoint mode and does not create an execution attempt.

```bash
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
git fetch --no-tags origin refs/heads/codex/lvef-multitask-revalidation:refs/remotes/origin/codex/lvef-multitask-revalidation
git merge --ff-only origin/codex/lvef-multitask-revalidation
```

## One tracked D3 entrypoint

The preflight is one short invocation with no positional commit and no private
path argument:

```bash
scripts/scc_finalize_lvef_phase1ef_d3.sh --preflight-only
```

Only after that invocation passes, run the single no-clobber offline capture:

```bash
scripts/scc_finalize_lvef_phase1ef_d3.sh --capture-current-environment
```

`--preflight-only` validates Git/state binding, preparation discovery, private
authorities, immutable capacity artifacts, attempt absence, and any existing
receipt. It writes no scientific artifact and does not constitute an execution
attempt. `--capture-current-environment` may create only the unique restricted
current-environment receipt, or validate an already valid receipt without
overwriting it.

Do not use a response-generated script, an OnDemand editor, SCP/SFTP/rsync,
terminal heredocs, base64, or browser text materialization. The separate
full-workflow preparer/dispatcher commands are not the Phase 1E-G D3 interface
and must not be invoked in this bounded phase.

## Bounded failure rule

If live SCC exposes a reversible implementation or contract defect, preserve
the failed no-clobber capture, make at most one code repair, revalidate and
synchronize, then use at most one final unique capture retry. Stop after a
failed final retry; do not add another authority layer.

Neither mode can rerun attempt 004, create attempt 005 or production attempt
006, contact a cloud service, submit `qsub`, read or extract a DICOM, use a GPU,
run EchoPrime inference, create embeddings, fit a model, generate predictions,
or access confirmatory performance. A 3–5-study DICOM canary remains separately
owner-authorized and must not be run through this interface.
