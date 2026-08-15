# Phase 1I-R3A sampled-signal repair and successor readiness

Status: **`NO_GO_PENDING_REPAIR_VALIDATION_AND_BOUNDED_LOCAL_REPLAY`**.

This record freezes the aggregate-safe failure evidence from the original
Phase 1I full-C3 attempt, states the correctness contract selected for R3A,
and defines the boundary for a later successor attempt. R3A is limited to
tracked code, synthetic and dependency-light tests, documentation, Git
synchronization, and installation validation. It does not authorize a live
DICOM read or replay, cloud access, object transfer, scheduler submission,
EchoPrime or GPU execution, model fitting, prediction generation, or
confirmatory-performance access.

## Governing and immutable authority

- Branch: `codex/lvef-multitask-revalidation`
- R3A starting commit: `b805fd1a403b3ff0503d09bb79d35b01805dd765`
- Original array job: `7183952`
- Original finalizer job: `7183953`
- Original attempt status: `FAILED_AT_BATCH_002_DICOM_EXTRACTION`
- Frozen original-attempt inventory: 158,288 files and 151,954,116,217
  bytes, with metadata-tree SHA-256
  `800ff8fa66949a919d00bf3f66b6aec16c3c244ff48e68dc47d866c924684bb3`

The original attempt and its job records are immutable evidence. R3A does not
rename, repair, adopt, delete, move, chmod, or write into that attempt. The
historical full-C3 authorization is not rewritten by this repair record, and
it is not authority for a successor execution.

## Frozen terminal evidence

Batch 1 completed its entire scientific and preservation contract:

| Aggregate field | Frozen value |
|---|---:|
| Studies | 250 |
| Declared/downloaded objects | 18,872 / 18,872 |
| Declared/downloaded bytes | 68,847,811,224 / 68,847,811,224 |
| Readable/unreadable DICOMs | 18,872 / 0 |
| Extracted cines | 10,383 |
| Clip/study embeddings | 10,383 / 250 |
| Exact pooling replay | `PASS` |
| Preservation / final ledger / finalization | `PASS` / `FINALIZED` / `PASS` |
| Raw DICOMs retained / extracted cache retired | `true` / `true` |

Batch 2 reached a different, narrowly localized boundary:

| Aggregate field | Frozen value |
|---|---:|
| Studies | 250 |
| Declared/downloaded objects | 18,196 / 18,196 |
| Declared/downloaded bytes | 64,974,548,122 / 64,974,548,122 |
| Download generation, size, MD5, CRC32C, and local SHA-256 gates | `PASS` |
| Missing/unexpected/unsafe/symlink/partial objects | 0 |
| Readable/unreadable DICOMs | 18,196 / 0 |
| Multiframe/single-frame objects | 9,937 / 8,259 |
| Successful/failed multiframe extractions | 9,936 / 1 |
| Raw DICOMs retained | `true` |

The sole failed multiframe object decoded successfully as 8-bit, three-sample
`YBR_FULL_422` under transfer syntax `1.2.840.10008.1.2.4.50`, using pydicom
raw pixels with Pillow and explicit YBR-to-RGB conversion. Its source sector,
source nonzero-signal, and source temporal-variation gates passed. Its
evaluated sampled nonzero-signal and sampled temporal-variation gates failed.
The affected study retained another successfully extracted cine, but that
fact is not a technical exclusion rule and does not permit bypassing the
failed object.

The exact supported failure class is therefore
**`SAMPLED_SIGNAL_QUALITY_GATE_FAILURE`**. The evidence does not support
relabeling it as corruption, a decoder or color-conversion failure, an
unsupported transfer syntax, a header failure, or intrinsically unusable
source content.

## Causal map and diagnostic boundary

The production extraction sequence is:

1. `_extract_one` reads the DICOM and calls `_normalize_dicom_pixels` to
   produce canonical contiguous `uint8` RGB plus decoder/color provenance.
2. `_mask_ultrasound_strict` constructs the ultrasound-sector mask and
   applies it to all source frames.
3. `_signal_quality_metrics` and `_require_signal_quality` evaluate source
   sector occupancy, retained nonzero signal, and temporal variation.
4. `_crop_resize` applies the ordinary centered square/zoom crop and cubic
   resize to 224 by 224.
5. `temporal_sample` applies the historical-compatible 32-frame rule:
   endpoint-inclusive integer linspace for long cines and ordered source
   frames followed by final-frame repetition for short cines.
6. `_signal_quality_metrics` and `_require_signal_quality` evaluate sampled
   nonzero signal and temporal variation, after which `write_npz_atomic`
   writes the authoritative array only on success.

The historical full-scale extractor followed
`dcmread`/`Dataset.pixel_array` -> `normalize_pixels` ->
`mask_outside_ultrasound` -> `crop_and_scale` -> `temporal_sample` ->
`np.savez_compressed`. Its center crop, zoom, cubic interpolation, and
linspace-or-tail-repeat temporal rule are materially the ordinary transforms
retained here. The relevant differences are that historical decoding/color
handling was implicit, masking exceptions fell back silently to unmasked
frames, and no source, post-crop, or sampled signal gate proved that the
written result retained usable signal. Historical write success therefore
cannot establish that this failed candidate would have retained signal, and
R3A does not restore that permissive extractor.

The original row provenance establishes successful decode/color conversion,
successful source masking and source gates, and failure of both final sampled
gates. It did not persist signal metrics immediately after crop/resize or a
step-specific failure substage. It therefore narrows the live failure to the
post-source-gate transform window but cannot, without a replay under the new
instrumentation, assign the object specifically to spatial signal loss,
temporal signal loss, or both.

R3A closes that diagnostic gap with mechanically distinct terminal states:

- `DECODE_OR_COLOR_CONVERSION_FAILURE`
- `SOURCE_SIGNAL_QUALITY_FAILURE`
- `SPATIAL_CROP_RESIZE_FAILURE`
- `POST_CROP_SIGNAL_QUALITY_FAILURE`
- `TEMPORAL_SAMPLING_FAILURE`
- `SAMPLED_NONZERO_SIGNAL_FAILURE`
- `SAMPLED_TEMPORAL_VARIATION_FAILURE`
- `OUTPUT_WRITE_FAILURE`
- `FALLBACK_PATH_PASS`
- `FALLBACK_PATH_FAILED`

Restricted row provenance records source, post-crop, and sampled nonzero and
temporal-variation counts; source sector-pixel count; selected preprocessing
path; temporal policy; and failure substage. Aggregate-safe outputs may report
only technical counts and status classes, never identifiers, locators,
filenames, or paths.

## Selected outcome-blind fallback contract

The ordinary centered crop and historical temporal sampler always run first.
An input that passes the ordinary final gates is returned unchanged. A bounded
fallback is eligible only after decode and color conversion succeed, all
source-level gates pass, and the ordinary sampled result fails an unchanged
signal gate.

The fallback has a fixed, pixel-only order:

1. When the post-crop evidence shows spatial signal loss, form a crop from the
   ultrasound-sector-mask bounding box, symmetrically zero-pad it to square,
   and cubic-resize it to the existing 224-by-224 spatial contract.
2. When temporal coverage is still required, apply the fixed stride-aware
   signal-coverage rule over 16 anchors and deterministically pair those
   anchors to the existing 32-frame encoder contract.
3. Evaluate the same final nonzero-signal, temporal-variation, shape, dtype,
   and encoder-visible gates. Write an authoritative NPZ only if every gate
   independently passes; otherwise fail closed.

The trigger and candidate order depend only on pixels, the derived sector
mask, frame order, and fixed constants. They do not inspect a study label,
target, split, embedding, prediction, outcome, or performance result. The
repair does not weaken either sampled signal gate, accept an all-zero or
temporally constant result, use a force override, or change decoder/color and
source-gate requirements. The selected path and any failed fallback substage
remain explicit in provenance.

## Exactness boundary

For every input already passing the ordinary path, the extracted frame array,
sampled indices, source-frame-count array, and their array-content hashes must
remain exact. R3A neither rewrites nor adopts Batch 1's previously validated
artifacts. A successful fallback output is intentionally a new preprocessing
result and is not asserted to match the failed ordinary candidate.

Expanded provenance rows, aggregates, schemas, manifests, and their file
hashes may necessarily differ because they encode the new step and path
fields. Synthetic regression and function-identity tests can establish that
the ordinary code path remains exact; they do not constitute a live replay of
the failed object or a new object-by-object validation of Batch 1.

## Replay and successor boundary

The future one-object route is limited to the already retained failed object,
a fresh owner-private no-clobber diagnostic root, DICOM preprocessing only,
zero cloud requests, zero embeddings, and aggregate-safe comparison of old
and repaired technical provenance. It must leave the original attempt
unchanged. Its state in R3A is **`LIVE_FAILED_OBJECT_REPLAY=NOT_RUN`**. The
tracked route is not executable authority by itself; a later, separate owner
authorization must bind the exact repaired commit, command, retained-object
authority, destination, and zero-cloud/zero-GPU boundary.

The selected future execution strategy is **`FRESH_SUCCESSOR_ATTEMPT`**. A
repaired commit changes the code, authority packet, plan and script hashes,
and attempt identity. Reusing Batch 1 or the verified Batch 2 download inside
a new commit would require cross-commit adoption and a recovery hierarchy
whose architecture and validation burden are disproportionate to repeating
approximately two of nineteen batches. A successor must instead receive a
new no-clobber attempt identity, current-commit packet and environment
authority, a fresh capacity receipt, complete scheduler commands, and
separate owner authorization.

The preserved failed extraction cache is classified exactly as
**`PRESERVED_IMMUTABLE_TERMINAL_FAILED_CACHE_NOT_ACTIVE_SUCCESSOR_INPUT`**.
A small general classifier may exclude it from an active-cache count only
when closed terminal-failure evidence is complete and internally valid.
Malformed, contradictory, or nonterminal partial caches continue to block
fail closed. This classification permits coexistence, not reuse, mutation,
deletion, or scientific adoption.

## Nonblocking performance disposition

The exact disposition is
**`BENCHMARK_INTRA_BATCH_PARAMETERS_AFTER_REPAIR`**. Batch 1 scheduler
accounting and authoritative wall time are unavailable, so there is no
measured bottleneck supporting a production optimization in R3A.

Future synthetic or separately authorized benchmarking may measure extraction
workers versus requested CPU slots, EchoPrime inference batch size, internal
object-transfer concurrency, and lightweight stage timings. Until then,
download, extraction, inference, preservation/finalization, queue, and
predecessor-wait parameters remain unchanged. Array concurrency stays at one;
raising it would not remove predecessor gating and could violate the
single-active-cache, storage-reserve, and failure-containment contracts.
Performance work does not block the correctness repair.

## Gate disposition and next action

| Gate | R3A disposition |
|---|---|
| Confirmed failure class | `SAMPLED_SIGNAL_QUALITY_GATE_FAILURE` |
| Download or decoder repair required | `NO` |
| Source signal gates changed | `NO` |
| Sampled signal gates weakened | `NO` |
| Outcome or label dependence introduced | `NO` |
| Original attempt and Batch 1 rewritten/adopted | `NO` |
| Live failed-object replay | `NOT_RUN` |
| New cloud requests / qsubs / GPU executions | `0 / 0 / 0` |
| Successor strategy | `FRESH_SUCCESSOR_ATTEMPT` |
| Performance disposition | `BENCHMARK_INTRA_BATCH_PARAMETERS_AFTER_REPAIR` |
| Full-C3 status | `NO_GO_PENDING_REPAIR_VALIDATION_AND_BOUNDED_LOCAL_REPLAY` |

The exact next action is
**`OWNER_REVIEW_OF_THE_REPAIRED_CODE_AND_ONE_BOUNDED_LOCAL_FAILED_OBJECT_REPLAY`**.
Only a separately authorized replay that passes the unchanged gates and
provenance contract can support a later decision about a fresh successor.
