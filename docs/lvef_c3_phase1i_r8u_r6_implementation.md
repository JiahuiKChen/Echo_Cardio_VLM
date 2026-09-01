# Phase 1I-R8U-R6 implementation record

R8U-R6 is an additive Batch-16 publication epoch. It preserves every prior
R8R/R8U artifact and consumes the failed R8U-R5 job `7388079` only as fixed
historical evidence.

The repaired locality rule separates directory observations into:

- stable publication identity: `device`, `inode`, `type`, `mode`, `uid`, and
  `gid`;
- diagnostic-only volatility: `nlink`, `size`, `mtime_ns`, and `ctime_ns`.

Stable-field drift, mount drift, target appearance, owner/mode drift, claim
drift, or a competing recovery role remains blocking. Volatile-only drift is
recorded by field name and equality flag without persisting raw device, inode,
UID, GID, size, or timestamp values.

The GPU publication chain is acyclic and fixed:

1. validate worker and portable-candidate authority;
2. create the exclusive no-clobber claim;
3. run and clean the empty-directory primitive probe;
4. capture final same-worker locality;
5. immediately recheck stable identity, target absence, and mount equality;
6. invoke exactly one authorized rename;
7. adjudicate source/target poststate and write the publication receipt;
8. begin the unchanged EchoPrime, preservation, retirement, and Batch-16
   finalization sequence only after publication passes.

The claim does not bind a future locality receipt. The primitive receipt binds
the claim, final locality binds the claim and primitive, and publication binds
all three. The claim-protected `os.rename` fallback is authorized only after an
`EINVAL` result from the `RENAME_NOREPLACE` probe. Cross-mount, permission,
I/O, and collision errors remain blocking.

The phase controller permits one CPU-only, non-array, ten-minute locality
probe and, after its receipt and `failed=0`/`exit_status=0` accounting pass,
one non-array GPU publication-resume submission. It performs one initial
qstat snapshot for the GPU submission and exposes no retry or Tasks 17–19
submission on the phase path.
