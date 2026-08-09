# SCC Autoclass supplemental adjudication commands

Status: one targeted metadata-only `buckets.get` plus offline adjudication; never Section 4 or qsub.

The existing Phase 1E-B/C run root, job-7104307 outputs, and current credentials remain the authorities. Before this block, synchronize the implementation commit, transactionally retire any superseded Section 3D receipt pair, run only Section 3A from `scc_phase1ebc_commands.md`, and run the existing metadata-only Section 3D authority revalidation only if repository policy requires a current-commit receipt. Do not repeat Section 3C.

Run the committed wrapper once in a strict child shell:

```bash
bash -lc '
set -euo pipefail
WORKTREE="/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/codex/lvef-multitask-revalidation)"
test -z "$(git status --porcelain --untracked-files=no)"
exec scripts/scc_run_lvef_c3_autoclass_adjudication.sh
'
```

The wrapper fails before network access if the unique attempt directory exists. It performs exactly one targeted bucket request, writes owner-private no-follow receipts, reports only the closed raw/effective states, and then runs the offline source/cost adjudicator. It never calls `objects.list`, qsub, a media endpoint, an object GET, BigQuery, DICOM processing, or modeling code.
