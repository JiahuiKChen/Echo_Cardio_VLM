# Echo Cardio VLM

Reproducible echocardiography ML pipeline for MIMIC-IV-ECHO with an EchoPrime embedding baseline and cloud/SCC-first execution.

## Scope

- Cohort construction from `physionet-data.mimiciv_echo` (BigQuery)
- Controlled DICOM acquisition from PhysioNet GCS buckets
- Cine extraction and key-frame selection
- Structured measurement export (beyond LVEF)
- LVEF baseline manifests and EchoPrime embedding baseline
- SCC + Vertex job wrappers for scale execution

## Repository Layout

- `scripts/`: end-to-end data, preprocessing, cohorting, and baseline runners
- `sql/`: SQL templates for note-link sidecars and cohort queries
- `docs/`: PRD, project plan, and EchoPrime repo audit
- `docker/`: GPU container definition and smoke-test script

## Quick Start

1. Run preflight checks:

```bash
./scripts/preflight_data_access.sh \
  --billing-project mimic-iv-anesthesia \
  --bq-project physionet-data \
  --echo-dataset mimiciv_echo \
  --note-dataset mimiciv_note \
  --hosp-dataset mimiciv_3_1_hosp
```

2. Build a cohort and download selected studies:

```bash
./scripts/run_cloud_echo_cohort.sh \
  --billing-project mimic-iv-anesthesia \
  --cohort-root outputs/cloud_cohorts/stage_d_500study \
  --download-root /path/to/download_root \
  --n-studies 500 \
  --seed 20260323 \
  --min-dicoms 40 \
  --max-dicoms 140 \
  --max-studies-per-subject 1 \
  --require-note-link true \
  --require-measurement-link true \
  --gcs-bucket mimic-iv-echo-1.0.physionet.org
```

3. Postprocess + baseline:

```bash
./scripts/run_cloud_cohort_postprocess.sh \
  --billing-project mimic-iv-anesthesia \
  --cohort-root outputs/cloud_cohorts/stage_d_500study \
  --download-root /path/to/download_root
```

## Data Governance

- Do not commit DUA-governed data, raw/derived patient-level outputs, or downloaded imaging assets.
- This repo is for code, SQL, configs, and documentation only.

## LVEF/multitask Phase 1E-E status

On `codex/lvef-multitask-revalidation`, prospective C3 production orchestration is implemented for offline/synthetic validation only. The immutable selected-source authority remains 4,530 studies, 335,984 verified objects, and 1,216,569,133,322 source bytes. Immutable parent research attempt `lvef_multitask_phase1ee_post_expansion_capacity_attempt_001` established the research quota/filesystem evidence; composite successor `lvef_multitask_phase1ee_post_expansion_capacity_attempt_002` hash-revalidated it and added current backed-control evidence without repeating the research commands. The 1,989,000,000,000-byte research quota, underlying filesystem, file quota, and frozen 200-GB reserve all pass. Full C3 nevertheless remains `NO_GO`: the 11,000,000,000-byte backed control tier has only 40,635,392 quota bytes available and fails its control-plane operating-margin gate. The preferred administrative adjustment is 50 GB backed plus 1,950 GB research; 25/1,975 GB is the minimum option. The owner-attested administrative composition keeps the purchased 1-TB SAAS allocation entirely on the research tier; the machine receipt proves the exact total quota, not its funding source.

Requester-pays planning costs remain frozen and owner-accepted at $136.101850/$142.906680/$171.488015 (low/base/high). No further cost work is required, and no DICOM-body transfer is authorized. Before one can be considered, the control-tier adjustment and fresh capacity receipt, verified backup/recovery witness, final current-commit authority packet/launch envelope, and separate owner authorization remain required.
