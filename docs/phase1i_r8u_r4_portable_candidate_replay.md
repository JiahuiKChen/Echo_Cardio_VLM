# Phase 1I-R8U-R4 portable candidate replay

R8U-R4 preserves the complete R8U-R3 epoch and separates two authorities that
R8U-R3 had combined.

The portable candidate authority is replayable across SCC nodes. It commits to
the five canonical control hashes, stage/input authority, semantic counts,
manifest-declared NPZ hashes, exact relative path sets, byte totals, and
portable file and directory metadata. File rows contain relative path, type,
mode, owner, link count, and size. Directory rows contain relative path, type,
mode, and owner. Device, inode, mount ID/source, hostname, ctime, mtime, and
directory link count are excluded. Device/inode/timestamps are still read twice
within one scan to reject replacement races, and no NPZ or DICOM body is read.

The immutable R8U-R3 seal remains at SHA-256
`cfb0b19044db53742c1fd6121f8d33b567f6a62c2661050cf4df2cc4e6f2cbf2`.
The failed job is `7364184`; its 110-byte merged scheduler log remains mode
`0644` with SHA-256
`bc2feef6398e3f9be408a77c80fe6eba38dad98506671d96b893371a5b59f824`.
That worker emitted only `CANDIDATE_FAILURE_UNRESOLVED`, so it did not persist
an exact differing node-local field. The aggregate-safe R4 diagnosis therefore
records an empty differing-field list, `NOT_PERSISTED_BY_FAILED_WORKER` as the
first-field state, exact portable content, and the closed node-local replay
classification. It never records a path, identifier, device, inode, mount
source, subject, study, or object value.

Publication locality is a distinct scheduled-worker authority. After an
exclusive no-clobber claim, the worker captures current source, parents, and
mount equality; verifies the target is absent and matching jobs/processes are
zero; runs the empty-directory `RENAME_NOREPLACE` probe; and rechecks opaque
in-process identities immediately before the one publication primitive. These
local values are never compared with login-node or prior-worker values.

The worker performs one portable candidate scan and reuses it through rename.
Post-rename continuity is proved by source absence, target presence, and the
same worker-local root identity. EchoPrime remains the first NPZ body/hash
reader and validates manifest-declared hashes before inference. Extraction,
download, DICOM-body access, model fitting, prediction, and confirmatory
performance paths are not reachable from the R8U-R4 resume entry point.

The R8U-R4 implementation commit must be exactly one direct child of
`a6e80b606a76dde2512a8eedf4eb8fa4f87211ef`. Its resume namespace, capacity,
authority, diagnosis, portable authority, claim, locality, probe, publication,
submission, accounting, and terminal receipts are fresh no-clobber artifacts.
The fixed future Tasks 17--19 and cohort-finalizer hooks consume the R8U-R4
terminal chain but are not submitted by the Batch-16 resume submitter.
