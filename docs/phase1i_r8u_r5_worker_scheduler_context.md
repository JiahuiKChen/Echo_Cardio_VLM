# Phase 1I-R8U-R5 worker scheduler context

R8U-R5 preserves the complete R8U-R4 epoch and separates the strict login-side
qsub environment from compute-worker identity. `build_qsub_environment()` is
unchanged for submitters. Compute workers instead use the kernel effective UID,
the sealed scheduler-account authority, the exact submission job and role, the
tracked runner and Python hashes, the implementation commit, and the sealed
qsub-environment hash. Ambient `USER`, `LOGNAME`, `HOME`, and `SHELL` values and
passwd lookup results are equality-only diagnostics; they do not replace the
effective-UID authority or enter the closed subprocess environment.

The fresh `r8u_r5_batch16_publication_resume` namespace first authorizes one
non-array, one-slot, CPU-only context probe with a ten-minute wall limit. The
probe performs the same worker context, qstat role, and numeric-UID process
checks as the GPU worker and cannot scan the candidate, read DICOM or NPZ
bodies, contact cloud storage, publish, extract, embed, preserve, or mutate the
scientific attempt. One bounded qacct adjudication must seal `failed=0` and
`exit_status=0` before the resume submitter is reachable.

After probe PASS, the login submitter validates the immutable R8U-R4 portable
authority for the existing 10,187-file candidate, requires an absent target,
captures the fixed 200-GB quota, physical, and file-slot reserves once, and may
issue one GPU resume qsub plus one initial qstat snapshot. It does not repeat a
candidate-tree scan. The GPU worker validates job, account, runtime, portable
authority, and one live candidate scan in that order before locality, claim,
primitive probe, or publication. Extraction, download, DICOM-body access,
model fitting, prediction, and confirmatory analysis remain unreachable.

Future Tasks 17--19 workers and the cohort finalizer consume the same sealed
scheduler-account authority and worker-context builder. Their submitters remain
strict qsub-environment consumers, but neither continuation workers nor the
finalizer reconstruct login identity from the ambient compute environment.
They are implemented for chain parity and are not submitted in the R8U-R5
Batch-16 resume phase.
