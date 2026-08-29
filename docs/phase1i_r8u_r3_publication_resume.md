# Phase 1I-R8U-R3 Batch-16 publication resume

Starting authority: `4fd8f4bf58ba56a5cc82893e80833cbc5c9332ff`.

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

Before publication, NPZ authority is metadata-only: exact extraction controls,
manifest-derived relative paths and declared NPZ hashes, owner/mode/link
topology, file sizes, stable inode metadata, and a move-stable projection.
Opening an NPZ body here would violate the phase contract. Cryptographic NPZ
body validation remains inside authorized EchoPrime inference after publication.
Preservation and retirement use the same fixed Batch-16 metadata authority for
the already-validated extraction cache and use manifest-declared hashes; they
do not reopen DICOM or extraction-NPZ scientific bodies.

Every new artifact binds the scientific, R8R, R8U base, projection-repair,
scheduler-log-repair, and current publication-resume commits. Historical R2
artifacts remain bound to the fixed scheduler-log commit and are never
re-rendered under R3. Future Tasks 17–19 and cohort-finalizer validators have a
closed R3 branch and reject mixed R2/R3 chains.

The login submitter performs only targeted receipt and metadata checks, one
capacity observation, one scheduler/process observation, one qsub, and one
initial qstat snapshot. It does not run the million-file attempt scanner, wait
for terminal accounting, poll, or submit Tasks 17–19 or the cohort finalizer.
The post-qsub snapshot is reduced to one closed, aggregate-safe projection and
bound into the no-clobber submission receipt; the worker validates that
projection instead of issuing a second qstat.
