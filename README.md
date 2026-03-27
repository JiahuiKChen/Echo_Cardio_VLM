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

