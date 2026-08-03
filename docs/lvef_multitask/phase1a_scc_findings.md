# Phase 1A SCC findings

- Evidence date: 2026-08-03
- Source commit: `62c982bb9fee602cdb7699a6cbebaab3c9852d1c`
- Aggregate packet: `phase1a_aggregate_packet.tar.gz`
- Packet SHA-256: `2cdb8dbf01ab37b6ba199db41c8ed45a696b45a6ec32c91189b525bb80ff4c41`
- Status: **Phase 1B adjudication in progress; confirmatory test access remains locked**

This report interprets aggregate-only SCC audits. It contains no subject/study identifiers, row-level labels or predictions, embeddings, DICOM paths, or restricted warning rows. The accepted version-10 abstract and the historical Git snapshot remain immutable historical authorities; this packet does not overwrite either.

## Packet verification

The transferred archive checksum independently matched its sidecar. The archive has 68 members: 58 regular aggregate files, nine directories, and the top-level safety-gate JSON. Every member is a relative path and a regular file or directory; no link, absolute path, or path traversal was present.

The supplied `aggregate_packet_safety_gate.json` reports `n_issues = 0`, `safety_gate_passed = true`, and `patient_level_files_included = false`. A fresh local execution of the same recursive header/key/type gate also scanned 58 files and found zero issues. The packet contains all ten expected audit status rows and the requested artifact, overlap, split, denominator, missingness, and leakage-evidence outputs. It contains no NPZ/Numpy/Parquet file, DICOM file, restricted audit directory, supplied source path, restricted source-row values, patient-level rows, labels, or predictions. Scoped specifically to `artifact_schema_checksums.json`, `row_values_emitted` and `supplied_paths_emitted` are both false; that file contains only schemas, shapes, sizes, and checksums of restricted authorities.

## Exit-status overview

Status 0 means the stated audit completed without a recorded scientific discrepancy. Status 1 means a discrepancy was recorded and is not a tooling failure. No audit returned status 2 or greater.

| Audit | Exit | Current interpretation | Gate class |
|---|---:|---|---|
| `batch_study_partition` | 0 | Nine batches exactly cover selected-minus-prior studies | Nonblocking |
| `artifact_audit` | 1 | Selected-cohort lineage is coherent, but clip-component provenance fails | Confirmatory blocker pending clip adjudication |
| `embedding_overlap` | 1 | Five selected studies lack cine/embeddings; 171 prior-stage studies are outside the selected set | Must lock exclusion/common-denominator rule; blocks an all-selected-imaged claim |
| `selected_split_coverage` | 0 | Exact selected-subject/split-map coverage | Nonblocking |
| `lvef_split_denominators` | 0 | LVEF labels, vision, and structured predictions have identical split sets | Nonblocking for the two supplied modalities only |
| `multitask_split_denominators` | 1 | Structured predictions include five imaging-ineligible studies that vision/fusion cannot include | Confirmatory blocker until common sets are locked |
| `lvef_partial_common_denominators` | 0 | Vision/structured keys and labels agree, including the `<40%` endpoint | Partial only; no fusion prediction authority |
| `multitask_common_denominators` | 1 | All 29 targets agree on common rows; set differences are caused by the five imaging-ineligible studies | Confirmatory blocker until intersection is locked |
| `train_missingness` | 0 | Train-only missingness outputs completed | Nonblocking for observed-label analysis; blocks natural-missing-label accuracy claims |
| `dependency_registry` | 0 | Provisional evidence matrix generated | Clinical panel lock remains blocked |

## Findings by audit

### 1. Batch study partition

- **Source authority:** selected-study manifest, prior Stage-D selected manifest, nine batch-study manifests, and batch manifest.
- **Proven:** 4,530 selected studies are one study per subject; Stage D contains 500 studies, of which 329 are selected and 171 are outside the selected set; the selected-minus-prior set is exactly 4,201 studies; nine uniquely named batches contain exactly those 4,201 studies with no missing, duplicate, outside-scope, identifier-integrity, or subject-ownership discrepancy.
- **Not proven:** successful pixel processing or embedding for every selected study; this audit establishes assignment, not downstream success.
- **Aggregate discrepancies:** none.
- **Restricted follow-up:** none for partition membership.
- **Gate:** nonblocking.

### 2. Artifact audit

- **Source authority:** the selected cohort and SCC manifests/stores listed in `artifact_schema_checksums.json`; the public-record and preselection eligibility artifacts were not supplied to this audit invocation.
- **Proven:** selected-cohort integrity is valid; all supplied stage subject/study mappings are valid; selected-stage containment has zero failures; LVEF label provenance is valid; 11 supplied artifact classes loaded successfully. The independent schema audit found 119/119 artifacts and 12/12 embedding-array/manifest pairs structurally valid, with zero blocking schema checks.
- **Not proven:** public-corpus-to-eligibility reconstruction in this run, full preservation-pack integrity, or exact clip-component provenance.
- **Aggregate discrepancies:** two artifact classes were `NOT_SUPPLIED` (`public_dicom_records`, `eligible_studies`); the evaluated clip-component union is invalid for the reasons below.
- **Restricted follow-up:** freeze and clip diagnostics only.
- **Gate:** blocks confirmatory access until the clip discrepancy is classified as benign metadata normalization or remediated by a prespecified, non-performance-based lineage decision.

### 3. Embedding eligibility/overlap

- **Source authority:** selected-study manifest, Stage-D membership, downloaded/readable/cine/extraction/clip manifests, and study-embedding manifest.
- **Proven:** the study store has 4,696 studies; 4,525 of 4,530 selected studies have embeddings; five selected studies lack embeddings; all five disappear at cine candidacy and none is lost later; the 171 embeddings outside the selected study set are prior-Stage-D studies. Together, `4,525 + 171 = 4,696` fully explains store membership.
- **Not proven:** why the five readable studies had no eligible cine, or whether rerunning the same deterministic cine-candidacy rules would change their status.
- **Aggregate discrepancies:** five `SELECTED_WITHOUT_STUDY_EMBEDDING / ABSENT_FROM_CINE_CANDIDATES`; 171 `EMBEDDING_OUTSIDE_SELECTED_SET / PRIOR_STAGE_STUDY`.
- **Restricted follow-up:** aggregate error/status counts for the five studies, without identifiers, are needed only to distinguish principled no-cine exclusion from a recoverable processing defect.
- **Gate:** lock the common-denominator and exclusion rule before confirmatory analysis. This does not support a claim that all 4,530 selected studies were embedded.

### 4. Selected split coverage

- **Source authority:** selected-study manifest and subject split map.
- **Proven:** exact equality of 4,530 selected subjects and split-map subjects; 3,171 train, 679 validation, and 680 test; no missing/duplicate subject, invalid split, or cross-split assignment.
- **Not proven:** denominator identity for a particular outcome or modality, which is assessed separately.
- **Aggregate discrepancies:** none.
- **Restricted follow-up:** none.
- **Gate:** nonblocking.

### 5. LVEF split denominators

- **Source authority:** selected LVEF manifest plus historical vision and structured prediction tables.
- **Proven:** LVEF labels, vision predictions, and structured predictions have exactly the same subjects and studies in every split: 1,997 train, 410 validation, and 426 test. There are no load or split warnings.
- **Not proven:** three-way identity involving fusion, because an equivalent historical fusion prediction table is not present.
- **Aggregate discrepancies:** none among the three supplied tables.
- **Restricted follow-up:** none for vision-versus-structured; fusion requires a separately authorized deterministic model-only regeneration after the design lock.
- **Gate:** nonblocking for the two-way historical audit; blocks a paired three-model incremental-value claim.

### 6. Multitask split denominators

- **Source authority:** historical long prediction tables and the subject split map.
- **Proven:** vision and fusion are identical in subject/study membership. Structured contains five additional selected studies: three train, one validation, and one test. Prediction-row counts are 75,553/16,285/16,099 for vision and fusion versus 75,627/16,309/16,122 for structured (train/validation/test). There are no invalid split assignments or load warnings.
- **Not proven:** that comparisons made on each model's native historical rows are paired or fair.
- **Aggregate discrepancies:** five subject/study set differences and 121 additional structured available-target rows (74 train, 24 validation, 23 test).
- **Restricted follow-up:** no identifier-level investigation is needed to explain the set difference; the common intersection must be materialized and checksum-locked before revalidation.
- **Gate:** confirmatory blocker until all modalities use the per-target common set.

### 7. Partial LVEF common denominator

- **Source authority:** independently reconstructed selected LVEF label authority plus historical vision and structured predictions.
- **Proven:** exact key/set identity and tolerance-aware continuous-label identity across all 2,833 studies and every split; zero label mismatches; zero subject/study ownership or split-assignment discrepancy; binary labels are consistent with historical `LVEF <40%`. The selected structured source has 2,836 numeric LVEF studies before imaging, and exactly three lack embeddings, leaving the 2,833 historical linked cohort.
- **Not proven:** fusion identity or paired fusion deltas.
- **Aggregate discrepancies:** none in the authorized two-modality comparison.
- **Restricted follow-up:** fusion only, after separate authorization.
- **Gate:** partial audit passes; the three-model LVEF claim remains locked.

### 8. Multitask common denominator

- **Source authority:** the unique 29-row task definition, wide and long panel authorities, and all three historical multitask prediction tables.
- **Proven:** every source contains the same 29 target names; the task definition has 29 nonblank unique rows; panel-wide, panel-long, and structured labels/rows agree; vision and fusion agree; all 116 target-by-split label comparisons (`all`, train, validation, test) have zero continuous-label mismatch at `rtol = atol = 1e-6`; subject/study ownership and split assignment agree on common rows; no missing keys, duplicate prediction keys, multi-subject study, or multi-split subject was recorded.
- **Not proven:** fairness of the historical macro comparison on model-specific native rows.
- **Aggregate discrepancies:** 344 of 377 identifier-set audit rows are nonidentical because panel/structured sources retain observed labels for up to five studies without imaging, whereas vision/fusion do not. Exactly 105 of 116 target-by-split combinations have a set difference: all 29 `all` combinations, all 29 train combinations, 24 of 29 validation combinations, and 23 of 29 test combinations. Validation already agrees for `arch_diam`, `height_cm`, `ivc_diam`, `mitral_e_velocity`, and `tricuspid_annular_plane_systolic_excursion`; test already agrees for `arch_diam`, `fs`, `height_cm`, `ivc_diam`, `left_ventricular_end_systolic_diameter`, and `mv_peak_a`. The reported 105 subject-study-pair failures and 29 subject-split-assignment failures are set-presence failures, not ownership or split disagreements. Thirty-three audit rows are exactly identical. The common intersection is always the vision/fusion set.
- **Restricted follow-up:** no label or ownership investigation is indicated; deterministically create and checksum-lock the common target/split intersection without examining performance.
- **Gate:** confirmatory blocker until that common-denominator authority is frozen.

### 9. Train-only missingness

- **Source authority:** the strict historical 29-task wide panel restricted to the 3,171 training studies.
- **Proven:** 75,627 of 91,959 task cells are observed (82.24%). Fourteen tasks have less than 10% missingness; nine have 10% to less than 25%; `sept_e_prime`, `arch_diam`, and `fs` have 25% to less than 50%; `ivc_diam` and `height_cm` have 60.14% and 64.02% missingness; TAPSE has 75.12% missingness. The four most common suppression-safe report patterns are missing `fs`/`ivc_diam`/TAPSE (287 studies, 9.05%), missing only `height_cm` (262, 8.26%), missing `height_cm`/TAPSE (149, 4.70%), and missing `ivc_diam`/`height_cm`/TAPSE (141, 4.45%). A further 1,357 studies (42.79%) belong to patterns suppressed because their individual frequency is below 10.

| Historical task | Observed train n / 3,171 | Missing |
|---|---:|---:|
| `body_surface_area` | 3,156 / 3,171 | 0.47% |
| `resting_sbp` | 3,142 / 3,171 | 0.91% |
| `resting_dbp` | 3,140 / 3,171 | 0.98% |
| `resting_hr` | 3,125 / 3,171 | 1.45% |
| `left_ventricular_end_diastolic_diameter` | 3,052 / 3,171 | 3.75% |
| `septal_thickness` | 3,046 / 3,171 | 3.94% |
| `inf_lat_thickness` | 3,045 / 3,171 | 3.97% |
| `la_dimen` | 3,013 / 3,171 | 4.98% |
| `sinus_diam` | 3,007 / 3,171 | 5.17% |
| `mv_peak_e` | 2,998 / 3,171 | 5.46% |
| `mitral_e_velocity` | 2,967 / 3,171 | 6.43% |
| `av_pk_vel` | 2,956 / 3,171 | 6.78% |
| `la_4ch_length` | 2,911 / 3,171 | 8.20% |
| `ra_length` | 2,906 / 3,171 | 8.36% |
| `ascending_aorta_diameter` | 2,807 / 3,171 | 11.48% |
| `lvot_diam` | 2,789 / 3,171 | 12.05% |
| `rv_diam` | 2,725 / 3,171 | 14.06% |
| `lvot_vti` | 2,657 / 3,171 | 16.21% |
| `mv_peak_a` | 2,643 / 3,171 | 16.65% |
| `tr_mmhg` | 2,614 / 3,171 | 17.57% |
| `tricuspid_regurgitant_peak_velocity` | 2,597 / 3,171 | 18.10% |
| `left_ventricular_end_systolic_diameter` | 2,494 / 3,171 | 21.35% |
| `lat_e_prime` | 2,386 / 3,171 | 24.76% |
| `sept_e_prime` | 2,372 / 3,171 | 25.20% |
| `arch_diam` | 2,264 / 3,171 | 28.60% |
| `fs` | 1,621 / 3,171 | 48.88% |
| `ivc_diam` | 1,264 / 3,171 | 60.14% |
| `height_cm` | 1,141 / 3,171 | 64.02% |
| `tricuspid_annular_plane_systolic_excursion` | 789 / 3,171 | 75.12% |

Within the provisional, not-yet-adjudicated families, availability is markedly asymmetric: TAPSE and IVC diameter are far sparser than routine chamber dimensions; height is sparse while BSA is nearly complete; and fractional shortening is sparse while LV end-diastolic diameter is nearly complete. These examples support family-aware masking and availability-stratified reporting, but they do not validate the provisional families or establish why fields were omitted.

- **Not proven:** missing-at-random assumptions, accuracy for genuinely unreported targets, or causal reasons for co-observation.
- **Aggregate discrepancies:** none; heterogeneity is the scientific finding. Strong co-observation includes TR gradient with TR peak velocity (Jaccard 0.9927), the two E-velocity names (0.9824), and septal with lateral e-prime (0.9825). These patterns do not themselves establish synonymy or formula dependence.
- **Restricted follow-up:** use only training data to lock empirical masking distributions; reference remeasurement is required for claims about naturally missing targets.
- **Gate:** nonblocking for observed-label revalidation; natural-missingness accuracy claims remain blocked. Structured, indication-dependent acquisition/reporting makes MNAR plausible, not proven.

### 10. Dependency registry

- **Source authority:** historical 29-task definition, restricted raw-to-canonical mapping schema, task metadata schema, and provisional family rules in code.
- **Proven:** the registry builder completed and the aggregate evidence matrix marks exact-target exclusion for LVEF plus all 29 historical tasks.
- **Not proven:** raw synonym completeness, canonical units, deterministic and near-deterministic relationships, clinical family boundaries, strict/pragmatic eligibility, thresholds, or error margins. Every row remains pending external evidence and clinician adjudication; the provisional strict/pragmatic dispositions are hypotheses.
- **Aggregate discrepancies:** none at execution level; scientific review is incomplete by design.
- **Restricted follow-up:** review suppression-safe raw-name/unit summaries and the OpenEvidence/clinician response, then version the registry and panels without using model performance.
- **Gate:** blocks final clinical panel and mask lock.

## Freeze integrity

The full `SHA256SUMS.txt` validation failed. The packet does not include the per-entry verifier output, so it cannot distinguish a missing member, stale/additional entry, byte mismatch, invalid relative path, or malformed checksum line. The preservation pack must not be called checksum-valid.

Eleven proposed duplicate pairs were independently byte-identical: five LVEF evaluation artifacts and six manifest/summary artifacts. This proves only those pairs. The freeze inventory has 14 relative members: the checksum file, five evaluation files, six manifest files, and two metadata files. The two metadata files were schema/checksum inspected but were not part of the duplicate-pair comparison. A safe allowlist-based per-entry diagnostic is required; the freeze pack must remain unchanged.

## Clip-component union provenance

The Stage-D-plus-nine-batch source union and the merged manifest each have 191,993 successful rows, 191,961 unique composite keys, 4,525 subjects, and 4,696 studies. Key sets and key multiplicities are equal; there are zero source-only keys, zero merged-only keys, and zero multiplicity mismatches. Subject sets, study sets, subject-study pairs, and ownership mappings are equal.

The failure is therefore not attributable to component ordering, merged-row ordering, an embedding-index rewrite, missing/extra key sets, or unequal multiplicity. The comparison is order-insensitive and explicitly excludes `embedding_idx`.

Two issues remain:

1. Each side has 32 duplicate-key excess rows, so the current composite locator is not unique even though its multiset is preserved.
2. 9,605 common clip keys have a non-index row-payload mismatch. `clip_keys_complete` is true, but the packet does not identify the differing column. Because key construction normalizes identifiers and strips locator text whereas payload comparison uses canonicalized original field values, the mismatch could involve formatting of a key-associated field or another payload field; it must not be narrowed further without the field-level diagnostic.

An aggregate field-level/component-level diagnostic is required before deciding whether this is a benign manifest-normalization defect or evidence of changed clip content. No embedding-value conclusion can be drawn from manifest metadata alone.

## Five-study attrition disposition

| Transition | Incremental selected-study loss | Interpretation | Provisional disposition |
|---|---:|---|---|
| Selection to download | 0 | All 4,530 selected studies have at least one downloaded record | No exclusion |
| Download to readable DICOM | 0 | All 4,530 have at least one readable record | No exclusion |
| Readable DICOM to cine candidacy | 5 | Five have no successful multiframe/cine candidate | Imaging-input unavailable; reason audit required |
| Cine to extraction | 0 additional | Every cine-eligible selected study reaches extraction | No reprocessing signal |
| Extraction to clip embedding | 0 additional | Every extracted selected study reaches a clip embedding | No reprocessing signal |
| Clip embedding to study aggregation | 0 additional | All 4,525 clip-embedded selected studies are aggregated | No reprocessing signal |
| Indeterminate/other | 0 | Stage location is fully assigned | None |

If the restricted aggregate diagnostic confirms genuinely absent eligible multiframe cines under the frozen rule, exclusion from imaging-containing modalities is principled and the primary paired estimand should use 4,525 imaging-eligible selected studies. Structured-only results on all available selected studies may be a clearly labeled secondary sensitivity, not a comparator for a paired fusion claim. If the diagnostic instead shows a deterministic processing defect, reprocessing must occur before test access and be documented without examining performance.

## Environment and checkpoint provenance

The schema packet establishes a currently available checkpoint alias `echoprime_encoder` corresponding to the extraction script's required filename `echo_prime_encoder.pt`: 138,642,379 bytes, SHA-256 `7ca32e8bfde248bd6d8c7e46fdb7440385169af4dc2f416b5de840bdc2e64f3b`. It also establishes float32, width-512 embedding arrays and internally consistent manifest/index shapes.

This does **not** prove that the same checkpoint bytes generated the historical embeddings. No historical run manifest in the aggregate packet links that hash to the embedding run, and the two freeze metadata files were exported only as schema/checksum summaries. No `pip freeze`, Conda export, package lock, Python/CUDA/cuDNN version record, or container digest is present. The repository setup script documents a procedure, not the historical environment authority.

Required limitation language: the study probes frozen historical EchoPrime-derived embeddings whose artifact shape and current checkpoint candidate are documented, but exact byte-level checkpoint-to-run linkage and the historical software environment have not yet been demonstrated; claims of byte-for-byte reproducibility are therefore not supported.

## Real-data findings versus synthetic tests

All counts above come from the verified aggregate SCC packet. Synthetic unit tests in `tests/` validate audit behavior only and are not evidence about MIMIC-IV-ECHO. Their messages, identifiers, and failure cases must never be mixed with the real-data findings table.

## Remaining lock conditions

Do not authorize confirmatory test access until all of the following are recorded in a versioned lock:

1. allowlist-based freeze checksum diagnosis and authority decision;
2. field/component classification of clip manifest duplicate-key and non-index payload mismatches;
3. adjudication of the five no-cine studies and the common modality estimand;
4. checksum-locked per-target common denominators;
5. environment/checkpoint linkage decision and limitation language;
6. external clinical adjudication of raw synonyms, units, formula dependencies, families, thresholds, and margins;
7. versioned strict, target-family-masked, and pragmatic panels;
8. versioned binary logistic penalty/C grid, compatible solver, class-weight rule, validation selection metric, and deterministic tie-break;
9. a prespecified probability-calibration policy and separate validation-only operating-point criterion with tie-break;
10. training-only structured feature-eligibility thresholds and explicit missing-indicator creation, retention, and suppression rules;
11. validation-only regression hyperparameter and paired subject-bootstrap lock;
12. separate authorization for any LVEF fusion prediction regeneration or any other model execution.

The authoritative detailed checklist is `phase1b_pre_revalidation_lock.md`; this summary must not be used to waive a condition recorded there.
