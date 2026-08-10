# Phase 1E-E capacity and prospective C3 orchestration lock

Status: **implementation complete offline; all production actions remain unauthorized**.

Phase 1E-E is limited to read-only SCC capacity confirmation, implementation,
synthetic validation, and authority freezing. It did not contact Google Cloud,
repeat an object listing or storage inventory, submit a scheduler job, download
an object body, decode a real DICOM, extract a real cine, run EchoPrime, create
an embedding, fit a model, generate a prediction, or access confirmatory
performance.

## Frozen source and cost authority

The current prospective source authority remains 4,530 selected studies and
subjects, 335,984 metadata-verified unique source objects, and
1,216,569,133,322 bytes. Missing, unexpected, inaccessible, ownership-
conflicting, and locator-conflicting objects remain zero. Historical-object
identity is not established because no historical object-level comparator was
available. Autoclass remains authoritatively default-disabled from the proven
raw state `KEY_ABSENT`.

The owner accepted the planning estimates of $136.101850, $142.906680, and
$171.488015 (low/base/high). Phase 1E-E performed no pricing, billing, credit,
or SCC storage-cost investigation and did not authorize expenditure.

## Post-expansion capacity ruling

The no-clobber post-expansion research capture records an exact
1,989,000,000,000-byte project quota, 150,387,011,072 exact allocated bytes,
and 1,838,612,988,928 quota-available bytes. The underlying research
filesystem records 2,092,672,483,328 physically available bytes. Against the
frozen 1,611,642,076,332-byte projected peak and 200,000,000,000-byte reserve:

- minimum effective quota gate: `PASS`;
- physical filesystem gate: `PASS`;
- projected 200-GB reserve gate: `PASS`;
- quota slack after the projected peak: 377,357,923,668 bytes;
- physical-filesystem slack after remaining writes and reserve:
  431,417,418,068 bytes.

The quota allocation is governed in decimal SI bytes. The management display
mixes a nominal decimal quota allocation with rounded usage presentation; exact
capacity decisions use the sealed `du -B1`/`df -B1` receipt, not subtraction of
rounded web-page values. The backed and research roots remain separate mounted
filesystems and neither is a bind mount.

The companion control-tier supplement records the exact current backed quota,
allocated usage, and quota availability in its closed aggregate. Its operational
gate remains `FAIL`: the present 11-GB allocation has insufficient safety margin
for the common Git repository, linked worktrees, environment/checkpoint
authorities, and bounded control-plane transients. All production data, partials,
temporary files, caches, state, logs, scheduler stdout/stderr, extracted data,
embeddings, and preservation artifacts are explicitly bound to the research
tier; the backed tier remains a read-mostly authority/control dependency.

A 25-GB backed / 1,975-GB research free-pool allocation preserves
363,357,923,668 bytes above the projected peak. The preferred 50-GB backed /
1,950-GB research allocation preserves 338,357,923,668 bytes. Both exceed the
unchanged 200-GB reserve and 1,811,642,076,332-byte minimum research quota. The
purchased 1,000-GB SAAS allocation remains entirely assigned to the research
tier. No quota was changed in this phase. The 50-GB backed option is preferred;
the 25-GB option is the minimum acceptable administrative alternative after a
fresh receipt proves it active.

## Implemented production control plane

The version-2 contract implements:

- one-to-one reconciliation of the frozen selected-source request manifest
  with the immutable job-7104307 enriched metadata receipt;
- rejection of cohort expansion, batch reassignment, new/missing objects,
  duplicate physical-source keys, ownership conflicts, and source-byte drift;
- a restricted deterministic 19-batch plan bound to cohort, source metadata,
  split, checkpoint, environment, contract, Git, and plan checksums;
- an exact-generation requester-pays downloader that cannot list the bucket,
  requires a stage-specific owner receipt, bounds request attempts, resumes
  checksum-bound partials, verifies size/generation/MD5/CRC32C/local SHA-256,
  and atomically promotes verified files;
- deterministic DICOM audit/extraction and encoder-only EchoPrime wrappers
  behind separate owner authorization receipts;
- finite float32 512-dimensional clip validation, mean study pooling, and the
  locked five-study no-multiframe disposition;
- a closed receipt-driven state machine and no-clobber per-batch resume
  authority;
- preservation-manifest construction, second-pass verification, cross-batch
  finalization, and closed aggregate safety gates;
- raw-DICOM deletion disabled, extracted-cache deletion disabled, and a
  separately tested cache-retirement eligibility decision;
- authority-bound SGE helpers whose work, temporary, cache, state, partial,
  log, stdout, and stderr locations are on `/restricted/projectnb` and which
  reject concurrent duplicate batch ownership.

The scripts are executable implementations, but their committed contract sets
every production authorization to false. Merely creating files cannot advance
state. Changed commit, contract, plan, source metadata, environment, checkpoint,
or manifest identity requires a new attempt.

## Authorization boundaries

The future execution packet keeps these decisions separate:

1. first-batch DICOM body transfer;
2. remaining-batch DICOM body transfer;
3. DICOM audit and cine extraction;
4. EchoPrime inference and study pooling;
5. preservation and any later extracted-cache retirement;
6. model fitting;
7. confirmatory-test access.

No scope is granted by this lock. Model fitting and confirmatory access are not
part of reconstruction authorization.

## Current ruling

Research quota, physical filesystem, reserve, and offline implementation gates
pass. Full C3 remains `NO_GO` because the backed control-tier gate does not pass
at the current 11-GB allocation and no first-body owner authorization exists.
After an approved 25- or preferably 50-GB backed-tier reallocation is active, a
fresh read-only control/research capacity receipt must pass before any selected
DICOM body request. No additional paid terabyte is indicated by the current
frozen plan.
