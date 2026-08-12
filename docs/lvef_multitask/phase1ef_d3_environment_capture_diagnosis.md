# Phase 1E-F-D3 current-environment capture diagnosis

Status: **diagnosis complete; minimal repair validated locally; no logical
attempt rerun or new execution authorized**.

## Immutable attempt-004 disposition

Phase 1E-F attempt 004 executed exactly once at commit
`6a814b3080f1159facf86ee60895117d187a41b7`. It passed its canonical seven-role
authority manifest, zero-scope command contract, capacity capture, quota,
physical-filesystem, 200-GB reserve, file-quota, and backed-control gates. It
then stopped at `CURRENT_ENVIRONMENT_CAPTURE` with direct status 2. No
environment receipt or downstream backup, restore, packet, launch, terminal
seal, or first-batch artifact was created.

Its immutable restricted capacity receipt remains 10,907 bytes with SHA-256
`b3bae07dcd6958b7cdfdd827a0de565ae0972753e04955fd03cd137b2e30ab62`.
Its immutable aggregate remains 5,399 bytes with SHA-256
`4d15b0a1a80188ce95d31659eca50cf18c8b6a1ebdef4022a56975f6e3e4bd20`.

One valid authorized `--execute` process ran. A later terminal-input buffering
event formed a malformed `--executeprintf...` token. The tracked dispatcher
rejected that token at `DISPATCH_MODE` with status 64; it passed no authority
validation, created no root, mutated no evidence, and was not a second logical
attempt execution.

## Bounded live diagnosis

The diagnostic used one unique owner-private root outside all attempt roots and
the exact established private preparation authority. It accessed software and
control metadata only. Network, Google Cloud, BigQuery, scheduler, GPU compute,
DICOM, embeddings, models, predictions, clinical data, and confirmatory
performance were not accessed.

The lexical EchoPrime virtual-environment launcher passed Torch, torchvision,
checkout authority, prior receipt, package inventory, CRC32C auxiliary runtime,
CUDA metadata, cuDNN metadata, receipt build, and restricted write. The exact
resolved regular interpreter target used by the dispatcher failed
deterministically at `TORCH_IMPORT` with sanitized exception class
`ModuleNotFoundError`.

Root cause: the dispatcher correctly proved that the launcher and resolved
target referred to the same executable authority, but then replaced the
launcher with the target for execution. Direct target execution discarded the
virtual-environment prefix and therefore its installed Torch package context.
The old `ENVIRONMENT_RUNTIME_IMPORT_FAILED` label was directionally related to
an import but materially under-specified because the same broad handler covered
every later stage.

## Minimal repair boundary

The repaired topology keeps two intentionally separate roles:

- the lexical virtual-environment launcher executes Python and preserves the
  validated package context, and its exact established SCC path is pinned so
  another virtual environment cannot substitute the same base interpreter;
- the resolved nonsymlink regular target remains the trusted byte/checksum and
  production-packet authority.

The capture utility now reports a closed failure stage, fixed sanitized code,
and allowlisted exception class without exception messages or tracebacks. Torch
and torchvision remain mandatory. The primary-runtime absence of
`google-crc32c` remains permitted because the independently pinned compiled
CRC32C runtime remains mandatory. CUDA and cuDNN metadata remain required;
their absence is not recast as success. Package inventory uses the same pinned
interpreter in a bounded isolated worker. Receipt schema v3 is unchanged and is
validated before a sibling temporary write and atomic no-overwrite promotion.

Attempts 001–004 remain immutable. The next unused logical identifier is
`lvef_multitask_phase1ef_post_reallocation_lock_attempt_005`, determined from
the preserved attempt sequence and collision check—not from any preparation
record suffix. It remains unprepared, unexecuted, and owner-unauthorized.
