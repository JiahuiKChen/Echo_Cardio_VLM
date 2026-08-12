# Phase 1E-F-D3 tracked recovery authority

Status: **implemented and validated offline; SCC synchronization and execution
remain separately owner-gated**.

## Disposition of the transport detour

The failed SCP/SFTP leaf is historical transport evidence, not scientific or
runtime authority. Its current absence has owner-provided administrative
disposition `EPHEMERAL_TRANSFER_LEAF_UNRESOLVED`. No missing-symlink cause is
inferred, and no further symlink forensics, command-line transfer retry,
OnDemand upload, or OnDemand editor use is required or authorized.

The canonical 5,786-byte fallback is content-safe for Git and is preserved
byte-for-byte as
`scripts/scc_finalize_lvef_phase1ef_d3_canonical_2088832.sh`, SHA-256
`873faf00b11658d6b11f5778088cfc028b663ec579c244f5a470168cb5086695`.
It has no shebang and pins the historical repair commit, so it is retained as
an immutable semantic authority rather than used as the current entrypoint.
`.gitattributes` disables text conversion for this exact path.

The closed one-to-one operation mapping is
`phase1ef_d3_canonical_operation_mapping.json`. The runnable successor is the
short tracked wrapper `scripts/scc_finalize_lvef_phase1ef_d3.sh`, backed by
`scripts/finalize_lvef_phase1ef_d3.py`.

## Successor safety contract

The successor accepts only the public expected 40-hex ending commit. It
recovers the three private runtime roles from exactly one established D3
diagnostic record and exactly one established preparation record. It never
prints those paths, contents, hashes, requester-pays values, credentials, or
tokens. Private files require owner authority, regular-file/no-follow checks,
and mode 0600 where applicable; private directories require mode 0700 or 2700.

Before Git mutation, it validates the branch, starting commit class,
historical-base ancestry, tracked cleanliness, attempts 001–004, absence of
attempt 005 and production attempt 006, both immutable capacity artifacts, the
exact lexical EchoPrime virtual-environment launcher, and any pre-existing
current receipt. An invalid existing receipt is preserved and blocks before a
fetch. The only permitted Git mutation is a fetch followed, when needed, by an
exact-origin, no-merge, fast-forward-only update.

Environment capture executes through the pinned lexical EchoPrime launcher.
The resolved regular interpreter remains the byte authority and is never
substituted as the launcher. A current receipt is atomically created only when
absent, or validated without overwrite when already present. Its full v3 schema,
exact governing commit, runtime authorities, and six false activity flags must
pass.

The entrypoint has no path to rerun capacity capture, backup/restore, create an
attempt root, contact Google Cloud or BigQuery, list or download an object,
submit a scheduler job, access a DICOM, run inference/GPU work, create an
embedding, fit a model, generate a prediction, or inspect confirmatory
performance.

## Separately gated SCC handoff

No command below was executed in this implementation phase. After owner review,
the SCC operator may first fast-forward the clean worktree to the exact ending
commit, then invoke the tracked D3 entrypoint once with that same public commit.
Private runtime authorities are resolved internally; no large script or private
path is pasted.

The recovery engine retains synthetic coverage for both the legacy and current
commit state transitions. Operationally, the tracked files do not exist in the
legacy checkout, so the separately authorized short Git fast-forward must run
first. The subsequent live entrypoint invocation is therefore expected to
report `SCC_D3_FAST_FORWARD=ALREADY_COMPLETE`; it must not be represented as
having transported or executed the new entrypoint from the legacy checkout.

```bash
: "${ENDING_COMMIT:?set the exact reviewed ending commit}"
cd /restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
git fetch --no-tags origin refs/heads/codex/lvef-multitask-revalidation:refs/remotes/origin/codex/lvef-multitask-revalidation
git merge --ff-only "$ENDING_COMMIT"
```

```bash
: "${ENDING_COMMIT:?set the exact reviewed ending commit}"
/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask/scripts/scc_finalize_lvef_phase1ef_d3.sh "$ENDING_COMMIT"
```

SCC synchronization and the single entrypoint invocation remain unauthorized
until a later prompt names the exact ending commit and entrypoint checksum.
