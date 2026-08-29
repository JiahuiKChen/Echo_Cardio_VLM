# Phase 1I-R8U-R3 Batch-16 publication resume

R3A starting authority:
`ce3326a23f149dd864c5aa534225b959d7b5abbe`. The immutable extraction event
remains bound to its producer implementation,
`4fd8f4bf58ba56a5cc82893e80833cbc5c9332ff`.

This repair resumes the sealed attempt from the complete 10,187-clip
Batch-16 extraction produced by scheduler job `7354951`. It does not expose a
general retry interface and does not authorize a cloud request, download,
DICOM-body read, DICOM extraction, partial-cache adoption, model fit,
prediction, or confirmatory analysis.

The sole live filesystem primitive probe runs inside the scheduled resume job,
immediately before publication. Running it before qsub would consume the new
R8U-R3 namespace and contradict the required pre-submission absence check. The
probe creates one empty source and one empty target-parent directory while the
target leaf remains absent. It attempts `renameat2(RENAME_NOREPLACE)` once,
classifies the exact errno and endpoint state, removes only the validated empty
probe directories, and seals one restricted receipt. The final submission
report therefore records the live primitive as `NOT_RUN`; the worker receipt
records its SCC result. A blocking classification is also sealed before the
worker stops, so the exact aggregate-safe errno evidence is not lost.

The preferred real primitive remains `renameat2(RENAME_NOREPLACE)`. Only a
probe result of `EINVAL`, `ENOSYS`, or `EOPNOTSUPP` admits one ordinary
same-filesystem `rename()` under a persistent exclusive no-clobber claim.
`EXDEV`, permission failures, I/O errors, unknown errors, collisions, and
ambiguous probe states remain blocking. After the one real call, endpoint
state controls the ruling. Source-absent plus an exact target passes even when
the server returned an error, and the rename is never repeated.

The R3A adjudication found no scientific contradiction. The exact first failed
adapter predicate was its manual requirement that a successful raw CSV row
have `failure_substage` in `{empty, None}`. The canonical producer writes
`NONE`, and the canonical production validator accepts and requires that
sentinel. The adapter also treated producer `output_relative_path` as relative
to the stage root, although both the producer and EchoPrime bind it beneath the
stage's `clips` root. R3A therefore parses the manifest once through the same
pandas/production-validator contract and derives closure as
`clips/<output_relative_path>`; it does not relax row identity, plan ownership,
or any scientific gate.

Before publication, NPZ authority is metadata-only: the exact five extraction
controls, canonical manifest-derived relative paths and declared NPZ hashes,
owner/mode/link topology, nonzero sizes, exact directory and file closure, and
a move-stable projection. Device, inode, and mount-ID values remain same-call
replacement/mount-equality guards and are not persisted across SCC nodes; the
portable seal retains owner/mode and hashed filesystem-source authority. Each
major candidate predicate emits its closed role-specific failure code.
Opening an NPZ body here would violate the phase contract. Cryptographic NPZ
body validation remains inside authorized EchoPrime inference after publication.
Preservation and retirement use the same fixed Batch-16 metadata authority for
the already-validated extraction cache and use manifest-declared hashes; they
do not reopen DICOM or extraction-NPZ scientific bodies.

Every new artifact binds seven epochs: scientific, R8R, R8U base,
projection-repair, scheduler-log-repair, fixed NFS publication-resume, and the
current candidate-authority repair commit. Historical R2 artifacts remain
bound to the fixed scheduler-log commit and are never re-rendered under R3.
Future Tasks 17–19 and cohort-finalizer validators have a closed R3 branch and
reject mixed R2/R3 chains.

The login submitter performs only targeted receipt and metadata checks, one
capacity observation, one scheduler/process observation, one qsub, and one
initial qstat snapshot. It does not run the million-file attempt scanner, wait
for terminal accounting, poll, or submit Tasks 17–19 or the cohort finalizer.
The post-qsub snapshot is reduced to one closed, aggregate-safe projection and
bound into the no-clobber submission receipt; the worker validates that
projection instead of issuing a second qstat.
