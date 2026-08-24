# JDIM Major Revision Tier-1 Tooling Design

## Scope freeze

This branch implements tooling for the JDIM-D-26-02840 major revision. It does
not execute analyses, inspect DICOM pixels, refit models, regenerate splits, or
edit submission files. All tests use synthetic records. Real row-level inputs
and outputs must remain in approved restricted storage.

Protocol constants are frozen at version `jdim-tier1-v1`, bootstrap seed
`20260824`, 2,000 subject-level resamples, and a parameterized manual-audit
default of 60 studies per target.

## Existing sources reused

### Structured labels and evaluation denominator

`scripts/run_tapse_lvot_vti_imaging_baseline.py` is canonical for the Phase 2
target and prediction contracts:

- `TARGETS` defines the exact `lvot_vti` and `tapse` measurement names, units,
  and current hard-extreme rules.
- `normalize_text()` and `to_clinical_value()` define name normalization and
  cm/mm conversion.
- `extract_target()` requires `subject_id`, `study_id`, `measurement`, and
  `result`; it parses numeric results, converts units, and aggregates repeated
  target rows by the median over `(study_id, subject_id)`.
- `load_splits()` requires `subject_id` and `split`, accepts
  `train`/`val`/`test`, and rejects subjects assigned to multiple splits.
- `join_model_frame()` joins targets to the study-embedding manifest by
  `study_id`, checks subject consistency when the manifest carries
  `subject_id`, and joins the frozen split by `subject_id`.
- `imaging_baseline_predictions.csv` is wide and contains, when enabled,
  `target`, `analysis_label`, `subject_id`, `study_id`, `split`,
  `target_value`, `n_target_rows`, `pred_null_median`, and `pred_ridge` plus
  target-range flags and optional view-filtering fields.

The Tier-1 cohort tool imports these canonical helpers instead of recreating
target preprocessing. The fixed-prediction tool accepts this wide schema and a
strict long schema (`target`, `model_name`, `subject_id`, `study_id`, `split`,
`y_true`, `y_pred`) without fitting any estimator.

### Imaging lineage

The established full-scale path is:

1. `scripts/scc_run_fullscale_pipeline.sh` queries MIMIC-IV-ECHO
   `echo_record_list` and `echo_study_list`, requires `measurement_id`, applies
   the pinned study-eligibility policy, and writes `all_eligible_studies.csv`.
2. Batch record exports contain `subject_id`, `study_id`,
   `acquisition_datetime`, and `dicom_filepath`.
3. `scripts/audit_mimic_echo_dicoms.py` writes `dicom_audit.csv` and
   `cine_candidates.csv`; readable multiframe rows have `read_ok=true` and
   `is_multiframe=true`.
4. `scripts/extract_mimic_echo_cines.py` writes extraction manifests containing
   `subject_id`, `study_id`, `dicom_filepath`, and `write_ok`.
5. `scripts/extract_echoprime_embeddings.py --encoder-only` writes a clip
   manifest with `embedding_idx`, `subject_id`, `study_id`, DICOM/clip path
   metadata, and `write_ok` for 512-dimensional frozen encoder embeddings.
6. `scripts/merge_batch_embeddings.py` merges per-batch clip stores and may
   include the earlier Stage D approximately 500-study store. It does not
   perform a study-level lineage reconciliation itself.
7. `scripts/aggregate_study_embeddings.py --method mean` keeps successful clip
   rows and produces one row per study with `study_idx`, `study_id`,
   `subject_id`, `n_clips`, and `embedding_l2_norm`.

The new cohort tool therefore models imaging and label stages as parallel
branches. It never treats counts from partially different batch artifacts as a
single monotonic funnel.

### Split provenance

The pinned split contract is the existing `subject_split_map_v1.csv`; it must
not be regenerated. The repository contains two historical deterministic
generation implementations (`global_subject_split_v1.py` and an inline
fallback in `scc_run_fullscale_pipeline.sh`). The actual SCC file, its summary,
SHA-256, schema, and generation-lineage declaration are consequently required
inputs. Missing lineage metadata returns
`BLOCKED_MIXED_OR_UNRESOLVED_LINEAGE`.

### Non-image comparisons

`scripts/run_phase2_nonimage_baselines.py` currently emits aggregate outputs
only. Paired non-image delta analyses are supported only if separate unchanged
row-level predictions are later available with exact study/subject/split keys.
No aggregate table is reverse engineered into predictions.

## New modules and commands

The importable package `scripts/jdim_tier1/` contains shared schemas, safety
checks, deterministic sampling, statistics, and provenance helpers. Thin CLI
entry points are:

- `scripts/reconstruct_jdim_cohort_flow.py`: aggregate cohort lineage and
  reconciliation.
- `scripts/prepare_jdim_input_audit.py`: config validation, deterministic
  target-stratified sampling, restricted annotation templates, technical pilot,
  and aggregate annotation summaries.
- `scripts/compute_jdim_fixed_prediction_metrics.py`: calibration,
  training-derived tertiles, and paired delta-MAE from unchanged predictions.
- `scripts/build_jdim_provenance_manifests.py`: separate restricted and
  export-safe reproducibility manifests.

## Input and output contracts

### Cohort flow

Required input roles are source study universe, pinned eligible-study universe,
expected DICOM records, one or more DICOM audits, extraction manifests,
named embedding-batch manifests, final study-embedding manifest, structured
measurements, frozen split map, canonical per-target analysis summaries, and a
lineage metadata JSON. Canonical keys are normalized string forms of
`study_id` and `subject_id`; DICOM-level deduplication uses `dicom_filepath`
when available. Multiple studies per subject are supported.

Aggregate outputs are `cohort_flow_summary.json`, `cohort_flow_stages.csv`,
`cohort_flow_target_splits.csv`, `cohort_flow_invariants.json`, and
`cohort_flow_provenance.json`. They contain counts, distributions, hashes, and
logical source roles only. An optional row-level reconciliation CSV is rejected
unless its destination is outside the Git worktree and explicitly designated
restricted.

### Manual input-content audit

`configs/jdim_input_content_audit_v1.yaml` is JSON-compatible YAML so it can be
validated with the Python standard library. Sampling inputs are final
target-plus-embedding cohort rows with `target`, `study_id`, `subject_id`, and
`split`. Optional reconstruction fields remain restricted.

Sampling uses SHA-256 ranking over protocol seed, target, split, and study key.
Default split allocations use deterministic largest remainder over actual
target-cohort split counts; explicit approved overrides are allowed. Physical
studies selected for both targets receive one opaque HMAC-derived audit ID and
one review. The HMAC key and all linkage rows remain restricted.

Restricted outputs include linkage, reader-order, study annotation, clip
annotation, adjudication, and technical-pilot manifests. Reader files never
contain target values, predictions, residuals, filenames, source IDs, or split
names. Export-safe outputs contain only sample counts, design weights,
study-level proportions and confidence intervals, cluster-bootstrap clip
summaries, reconstruction-failure counts, and agreement statistics.

### Fixed-prediction reviewer metrics

The tool reads unchanged saved predictions. It verifies unique study/model
keys, identical target values across paired files, exact split agreement, and
subject consistency. Calibration is defined as
`observed = intercept + slope * predicted`. Bootstrap sampling is by subject
and retains every study for each sampled subject.

Tertile boundaries are derived once from training labels, written to
`training_tertile_boundaries.json`, and applied to held-out test labels. Test
outputs are named lower, middle, and upper training-distribution tertiles.
Paired improvement is `MAE_comparator - MAE_imaging_Ridge`; positive values
favor imaging. Ineligible non-image comparisons produce
`PAIRED_NONIMAGE_COMPARISON_UNAVAILABLE` with aggregate reason counts.

Only aggregate CSV/JSON files are written. No model library, alpha argument,
threshold optimizer, or prediction writer is present.

### Provenance manifests

A versioned specification identifies logical inputs and outputs. The restricted
manifest may contain absolute paths, exact schemas, command arguments, and
environment details and must be written outside the worktree. The export-safe
manifest contains logical roles, hashes, byte/row counts, schema hashes,
versions, aggregate split counts, and overlap results, but no local paths or
row-level identifiers.

## Safety classification

Must remain restricted:

- all source, subject, study, DICOM, and clip identifiers;
- all filenames, local/remote paths, timestamps, target values, predictions,
  residuals, candidate values, and split assignments;
- row-level batch reconciliation, audit linkage, annotations, adjudication,
  pilot records, and paired prediction tables;
- DICOM pixels, screenshots, and reconstruction artifacts.

Aggregate-safe after disclosure review:

- stage/target/split counts and multiplicity distributions;
- performance points and confidence intervals;
- study-level audit proportions and agreement statistics;
- configuration, code, checkpoint, and input/output hashes;
- software versions and path-free logical provenance.

## Versioning and hashing

- Protocol/config version: `jdim-input-content-audit-v1`.
- Seeds: `20260824` for audit ranking and reviewer metrics.
- All input/config/output artifacts are SHA-256 hashed in streaming binary mode.
- Safe manifests identify files by logical role, never basename or path.
- Frozen tertile JSON records the training source split, quantile method,
  sample counts, and source-file hash.
- Real runs require a clean Git checkout and record branch and commit.

## Tests

Synthetic `unittest` coverage includes duplicate studies; multiple studies per
subject; subject and split conflicts; repeated label rows with median
aggregation; mixed and overlapping batch lineages; legacy members inside and
outside the canonical universe; missing release/split provenance; valid
parallel nonmonotonic flows; rejection of forced linear flows; deterministic
audit allocation/overlap handling/overrides/weights; sparse agreement and
clustered clip summaries; unsafe output destinations and forbidden safe
schemas; train-only tertiles; refit attempts; strict non-image pairing;
delta-MAE direction; and subject bootstraps with multiple studies.

Validation also runs Python compilation, repository shell syntax checks for any
new shell files, available formatter/linter commands, `git diff --check`, and a
tracked-content safety scan.

## Unresolved assumptions and stop conditions

- The exact MIMIC-IV-ECHO release string and official source denominator must
  be supplied in lineage metadata; they are not inferred from directory names.
- The actual frozen split-map generator/summary and SHA-256 must be confirmed
  from SCC artifacts.
- Batch manifests must label the earlier Stage D store separately from every
  full-scale batch.
- Canonical target summaries must correspond to the unchanged primary
  all-clips runs.
- Real non-image paired predictions may not exist; absence is nonfatal and is
  reported explicitly.

Missing or conflicting release, imaging, label, batch, or split provenance;
conflicting study-to-subject mappings; unexplained target/split totals; or an
attempt to write restricted rows inside the worktree returns
`BLOCKED_MIXED_OR_UNRESOLVED_LINEAGE` or another explicit fail-closed status.
No manuscript-ready cohort flow is emitted after a blocking invariant.
