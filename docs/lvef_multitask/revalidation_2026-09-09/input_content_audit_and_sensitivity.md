# Input-content audit and sensitivity design

Status: **source audit complete; pixel-content inspection not performed**. This proposal does not authorize raw-data downloads, regeneration of the cohort, or new encoder inference. It leaves the completed C3 representation unchanged and is separate from the existing ASA statistical critical path.

## What the actual production code establishes

Reviewed at completed producer `cda842d04cc18eb3669ad5377c31a6e955fc43cb`: [production extraction/encoding](../../../scripts/lvef_c3_production_stages.py), [shared extraction implementation](../../../scripts/lvef_reconstruction_smoke.py), and [preservation/pooling](../../../scripts/preserve_lvef_c3_production_batch.py). The production wrapper calls these extraction and encoder-input functions; similarly named view-diagnosis scripts are not authority for C3.

| Stage | Observed implementation | What remains unproved |
|---|---|---|
| DICOM decoding | Stored-color raw pydicom API, deterministic decoder selection, explicit RGB authority; grayscale replication; supported YBR converted once | Clinical acquisition mode and quantitative content cannot be inferred from photometric interpretation |
| Sector mask | `_mask_ultrasound_strict`: RGB luma occupancy, erosion, first/last-frame change, dilation, contour hull/flood-fill; outside-mask pixels zeroed | No OCR, number detector, caliper/trace detector, or semantic removal of measurement annotations |
| Ordinary crop | `_crop_resize`: central square crop, then 10% of each edge, cubic resize 224×224 | In-sector overlays can survive; some anatomy can be cropped; no view-specific framing proof |
| Sampling | `temporal_sample`:32 endpoint-inclusive integer-linspace frames for long cines; preserve source order then repeat last frame for short cines | This is not ECG/beat alignment or an independent view/mode selection |
| Bounded fallback | Inclusive sector bounding box with symmetric square zero-padding if ordinary spatial signal fails; deterministic16 signal anchors pair-repeated if temporal fallback needed | Pixel-signal recovery does not establish clinical interpretability, remove text, or identify a mode |
| Encoder input | `_prepare_encoder_input`:float32 RGB, mean `[29.110628,28.076836,29.096405]`, SD `[47.989223,46.456997,47.20083]` on 0–255 scale; indices 0, 2, …, 30 →3×16×224×224 | Normalization does not suppress quantitative cues; inspecting only source frames misses sampling/cropping consequences |
| Encoder and pooling | Strict checkpoint load; evaluation/no-gradient mode; 512 float32 clip vector; stable-key float64 mean then float32 study vector | Mean pooling is not view-informed retrieval; clip-number/mode mix can encode acquisition workflow |
| Inclusion | Multiframe technical eligibility and explicit dispositions, selected study membership, canonical clip identity | No B-mode-only, Doppler-only, A4C-only, diagnostic-quality, or quantitative-overlay-free clinical filter established in this producer path |

The completed receipts establish mask application, provenance, signal gates, and technical accounting. They do not establish that every input is anatomy-only. In particular, a static annotation inside a retained moving sector can remain even when the mask removes much of the surrounding display. Color-flow pixels are not necessarily removable metadata. Calipers, numeric values, spectral envelopes, ECG strips, M-mode sweeps, vendor text, measurement tables, and other acquisition cues require actual-content review; none is asserted present or absent here.

## Bounded blinded review proposal

Freeze this proposal and its sampling manifest before any content-dependent model evaluation. Use the completed, validated clip-membership manifest and recorded source hashes. Do not sample using labels, errors, predictions, or apparent model disagreement. The proposal's fixed seed is 20260909.

1. Probability sample 60 study keys from the 4,525 eligible studies, blinded to split/labels/performance, then up to 4 clips uniformly without replacement per study: at most 240 clips. Keep inclusion probabilities and study clusters for descriptive prevalence intervals. A study with fewer clips contributes all available clips. This cap is a feasibility choice, not a power claim.
2. Add up to 24 clips selected by metadata-only rare preprocessing/decoder strata, including bounded fallback paths when available. Mark this as an enriched audit; do not combine its unweighted frequencies with the probability sample. Never inspect a historical partial as if it were a current clip.
3. Present the exact 16 encoder-visible RGB frames as a cine/contact sheet reconstructed from the validated 32-frame artifact **only if it is already available through authorized SCC access**. Record its hash and input indices. Where original frames are already available, an optional paired pre/post view may assess crop/mask effects, separately from the encoder-input audit.
4. Two qualified readers independently classify content, blinded to subject identifiers, split, structured labels, predictions, performance, and one another. Image-embedded numbers may be inherently visible; readers record only cue category/presence and whether it appears legible, never transcribe patient data or measurement values. Disagreements receive a third adjudication or an unresolved label. Record reader qualifications/date without treating this as the existing clinical-registry signoff.

The production cache was retired after preservation. The available reconstruction evidence is not a promise that the sampled NPZ pixel artifacts remain readable. First establish whether authorized, preserved inputs already exist; otherwise record this audit as pending access. Do not reconstruct the full cohort, download raw data, or silently substitute historical cached pixels. Any proposed bounded regeneration from source requires a separately authorized, hash-bound plan and is outside this draft.

## Annotation fields and outputs

Restricted row: random audit token; clip hash; preprocessing stratum; image mode (2D grayscale, 2D color, spectral Doppler, M-mode, 3D/multiplane, other, uncertain; permit mixed modes); view (A4C/A2C/A3C/PLAX/PSAX/subcostal/suprasternal/other/uncertain); numeric measurement text (absent/present unreadable/present legible/uncertain); calipers; tracing contour/envelope; ECG/timing strip; measurement table; vendor/workflow text; anatomy truncated; retained signal adequacy; each reader's assessment and final adjudication. Record whether the cue survives in at least one encoder-visible frame, not only a discarded source frame.

Git-safe output: sampled studies/clips, selected/completed/missing counts by allowed stratum, agreement/confusion summaries, cue/mode/view category counts with denominators, uncertain fraction, sampling method and hashes, and reader signoff status. Do not export pixels, identifiers, source paths, text read from pixels, or uncommon combinations that violate the aggregate-safety policy. Presence counts characterize this sample, with uncertainty; zero observed cues does not prove absence across 184,570 clips. A 60-study sample does not establish complete view coverage for every study.

## Prespecified sensitivity options — cost and interpretation

| Option | What can be reused | What must be newly established | Interpretation and authorization |
|---|---|---|---|
| Stratified description only | Existing membership/provenance and permitted inspected inputs | Blinded annotations; denominators/uncertainty | Lowest burden; reports uncertainty without changing primary models |
| Re-pool approved clip subsets | Existing validated per-clip vectors; no encoder execution | Fixed annotation/membership rule, exact clip-vector index join, no duplicates, stable pooling, resulting subject eligibility and hashes | New representation sensitivity; regenerate training transformations/models and fixed-test evaluation under its own lock; do not call unchanged primary |
| A4C/mode-restricted paired subset | Existing clip vectors only if independently validated view/mode labels cover the proposed subset | Independent content selection, missing-view reasons, same subjects for all comparisons, support | View availability defines a new estimand; do not filter by EF/error or extrapolate sparse sampled labels to unreviewed clips |
| Pixel masking / altered crop or sampling | Original permitted pixels only | Frozen mask rule; verification of anatomy retention; new preprocessing/encoder execution and output provenance | Existing embedding cannot be “unmasked”; a separate inference authorization/resource budget is required |
| New comparator encoder | Compatible permitted pixels | Checkpoint/license, matched-input and native-input contracts, bounded benchmark and paired analysis lock | Separate proposal; no automatic launch |

Clean-clip exclusion cannot be inferred from a 60-study convenience sample or applied to unseen clips. For a probability-sample-only sensitivity, disclose the restricted sample and support; for a wider sensitivity, first validate content membership over its entire intended roster. Do not drop subjects merely because the primary result is unfavorable. New rules chosen after test inspection are exploratory and require explicit disclosure or independent validation.

Primary interpretation remains observed-report-label prediction from the available postprocessed cine content. A credible absence audit would strengthen a narrower claim about measured cue prevalence; it would still not prove that the encoder learned direct anatomy measurement or eliminate all acquisition-workflow information.
