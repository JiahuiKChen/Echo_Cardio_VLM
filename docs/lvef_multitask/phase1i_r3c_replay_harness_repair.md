# Phase 1I-R3C replay-harness authority repair

Status: **`NO_GO_PENDING_REPLAY_OR_FRESH_SUCCESSOR_AUTHORIZATION`**.

R3C is a narrow authority and validation repair for the retained failed-object
replay. It does not reopen the R3A scientific implementation and does not
authorize cloud access, object listing or download, scheduler submission,
EchoPrime or GPU execution, embedding generation, model fitting, prediction,
confirmatory-performance access, or a full-C3 successor attempt.

## Frozen authority and preservation boundary

- Branch: `codex/lvef-multitask-revalidation`
- Starting commit: `18ebc03e64b0964c13eaa26bfb055c6103cf18ad`
- Parent failed-execution commit:
  `b805fd1a403b3ff0503d09bb79d35b01805dd765`
- Original array/finalizer jobs: `7183952` / `7183953`
- Original-attempt inventory: 158,288 files, 151,954,116,217 bytes, zero
  symlinks, and zero nonregular entries
- Historical opaque metadata-tree SHA-256:
  `800ff8fa66949a919d00bf3f66b6aec16c3c244ff48e68dc47d866c924684bb3`

The historical digest preimage was not retained and is not reconstructed with
a new algorithm. The original attempt, the original jobs, and any later R3C
diagnostic root remain immutable evidence; diagnostic output is never adopted
as production reconstruction input.

The scientific failure remains
**`SAMPLED_SIGNAL_QUALITY_GATE_FAILURE`**: decode and explicit color
conversion passed; all three source gates passed; and both ordinary sampled
gates failed. Decoder behavior, color conversion, sector masking, ordinary
crop/resize and temporal sampling, fallback transforms, all signal gates, and
the EchoPrime input contract remain unchanged.

## Corrected object-scope contract

The live scope is exactly one unique retained local DICOM object, not one
filesystem open. A stable no-follow content-hash pass and a pydicom
parse/decode pass over that same validated object are permitted. A second
distinct DICOM object is prohibited. The replay records the unique-object
count, local content-hash passes, and pydicom decode invocations separately.

## Harness repair contract

Before any DICOM-body read, the replay binds the fixed failed row to the
frozen selected-source authority, full batch plan and exact batch assignment,
per-object verification receipt and its ledger hash, expected size,
generation, remote MD5, remote CRC32C, retained local SHA-256, and stable
owner-controlled regular-file identity. No caller can provide an object,
study, batch, manifest, or output filename, and no network revalidation is
permitted.

The frozen selected-source authority independently anchors the remote
metadata tuple. Agreement among a plan, ledger, and receipt that were derived
from one another is not sufficient by itself, and remote generation is not
claimed to be locally recomputable.

Release-time validation records the changed-file hashes and reconfirms every
unchanged frozen principal hash. The no-body `--preflight-only` mode validates
the synchronized Git authority, original-attempt inventory, the complete
object authority, the approved diagnostic parent, future root absence,
effective-private mode semantics, output absence, and zero prohibited scopes.
It creates no root or file and performs zero DICOM-body reads or decodes. Its
sole success marker is
`R3C_REPLAY_PREFLIGHT=PASS_ZERO_BODY_NO_ROOT`; any other result blocks live
execution.

The diagnostic parent and future root must be owner-controlled nonsymlink
directories with no group/other access and mode exactly `0700` or inherited
setgid `2700`. The root is checked while absent, created exactly once without
clobbering, and revalidated after creation. Created files are mode `0600` and
published no-follow and no-clobber.

The production extractor must reproduce both ordinary sampled gates as
`FAIL` before fallback success can be accepted. A mixed result, both gates
passing, or an earlier decode/source failure is inconsistent live evidence
and stops with a safe role-specific code. Only the existing R3A fallback may
then run, and it must pass the unchanged full-32 and encoder-visible-16 signal
gates with `FALLBACK_PATH_PASS`.

The diagnostic NPZ is reopened through the canonical production validator.
Validation covers stable private-file identity, exact container hash, exact
and nonduplicated archive members, frame shape and dtype, sampled-index and
source-frame metadata contracts, nonempty finite content, internal array
hashes, and consistency with the restricted preprocessing-path,
temporal-policy, fallback, source-authority, and signal-gate row authority.
The canonical archive remains the three-array production schema; the coupled
restricted row, rather than new NPZ members, carries preprocessing and source
provenance. Successful status is
`R3C_DIAGNOSTIC_NPZ_VALIDATION=PASS_DEEP_REOPEN`.

## Release and live boundary

The release allowlist is exactly the replay entrypoint and its focused test,
`lvef_reconstruction_smoke.py` and its focused test for the shared canonical
NPZ validator, and this document. No pinned scheduler entrypoint changes, so
the canary scheduler plan and every other config remain byte-identical. No
scheduler topology, resource, callable, concurrency, or authorization change
is permitted.

At this tracked repair-document boundary, SCC synchronization, R3C preflight,
live replay, and successor execution remain `NOT_RUN`. Performance
configuration is unchanged, no performance benchmark is executed, and any
later authorized successor retains the unchanged throughput configuration.

After focused development tests stabilize, the complete final validation is
run exactly once. It includes focused replay, extraction/provenance, canonical
NPZ-validator, full-sequential and canary integration tests; the maintained
dependency-light suite; native pytest when available; Python and Bash syntax;
strict JSON/YAML/CSV and duplicate-key/column checks; no-follow/no-clobber and
safe-export gates; credential and identifier scans; `git diff --check`; and
an exact staged-file review. Only a passing, narrowly staged diff may be
committed and synchronized.

After synchronization, exactly one no-body preflight is permitted. If and
only if it passes, exactly one live `--execute` invocation is authorized. The
scientific invocation is consumed when its Python process begins. On any
failure, preserve the sole diagnostic evidence, report the first safe code,
and do not retry, choose another object, alter a threshold, or try another
decoder or preprocessing variant.

Aggregate-safe reporting is limited to authority and gate states, unique
object/hash/decode counts, safe preprocessing and temporal-policy classes,
NPZ bytes and SHA-256 under a nonidentifying artifact role, the fixed receipt
basenames with bytes and SHA-256, original-attempt inventory equality, and
zero-effect counters. Object-derived names, identifiers, locators, and private
paths are not exported. In particular, an NPZ basename derived from a clip or
object key remains restricted even though the requested return names that
field; the aggregate-safe return uses a fixed artifact role or an explicit
withheld-object-derived disposition instead.

Only a fully passing replay permits a read-only successor review. The sole
permitted strategy is **`FRESH_SUCCESSOR_ATTEMPT`**: a new no-clobber attempt
must reconstruct all 19 batches sequentially, preserve the failed attempt and
R3C diagnostic evidence, retain all throughput settings and the
single-active-cache policy, and count ordinary, spatial, temporal, combined,
and failed fallback dispositions. It still requires a separate copy-ready
owner authorization and cannot make modeling or confirmatory paths reachable.
