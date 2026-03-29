# SCC Operations Guide: Lessons Learned

Last updated: 2026-03-28

## Hard constraints

| Constraint | Detail | Impact |
|-----------|--------|--------|
| Login node CPU limit | Interactive processes killed after 15 min CPU / 25% lifetime | All heavy work must use `qsub` batch jobs |
| Restricted partition | `/restricted/project` and `/restricted/projectnb` only visible from scc4 | Always connect via scc4 (OnDemand, VPN, or jump) |
| Storage quota | 200 GB backed-up + 800 GB non-backed-up = 1 TB total | Purge raw DICOMs after encoding; batch downloads |
| GPU CC minimum | PyTorch 2.11.0+cu130 requires compute capability ≥ 7.5 | Always request `-l gpu_c=8.0` (avoids V100 CC 7.0) |
| GPU memory | V100 has only 16 GB; MViT v2 + batch 8 needs ~19 GB | Always request `-l gpu_memory=48G` (targets A40/A6000) |

## GPU selection rules

Always use these qsub flags for GPU jobs:

```bash
-l gpus=1 -l gpu_c=8.0 -l gpu_memory=48G
```

This targets A40 (48 GB, CC 8.6) or A6000 (48 GB, CC 8.6) nodes, which have:
- Best availability (~48 and ~50 typically free)
- Full PyTorch compatibility
- 3x the VRAM of V100

For heavy Phase 5 training, request A100-80G:

```bash
-l gpus=1 -l gpu_c=8.0 -l gpu_memory=80G
```

Check live availability: `qgpus -v -s`

GPU compute is **free** (friendly user mode — only CPU is charged).

## Batch job template

All batch job scripts must follow this pattern:

```bash
#!/usr/bin/env bash
set -x

cd /restricted/project/mimicecho/code/Echo_Cardio_VLM

# Relax strict mode during module init (profile scripts reference unset vars)
set +eu
for init_file in \
    /etc/profile.d/modules.sh \
    /etc/profile \
    /etc/bashrc \
    /usr/share/Modules/init/bash; do
  if command -v module >/dev/null 2>&1; then
    break
  fi
  if [[ -f "${init_file}" ]]; then
    source "${init_file}" >/dev/null 2>&1 || true
  fi
done
# Re-enable strict mode AFTER module init
set -euo pipefail

module load python3/3.10.12
module load google-cloud-sdk/455.0.0  # if needed

source ./scc_env.sh

# ... actual work here ...
```

**Why:** `set -euo pipefail` before `source /etc/profile` kills the job instantly
because profile scripts reference unset variables (triggers `set -u`).

## Download resilience

GCS downloads use `gsutil -m cp -r` (not `gcloud storage cp --recursive`) because
`gcloud storage cp` has a `.gstmp` temp file race condition on GPFS network filesystems.

All `find` commands in download loops use `2>/dev/null` to suppress transient file errors.

Downloads have 3-attempt retry with `.gstmp` cleanup between retries. Failed studies
are logged as `download_failed` and skipped, not fatal.

## Resource request cheat sheet

| Job type | Runtime | Cores | Memory | GPU | Script |
|----------|---------|-------|--------|-----|--------|
| Canary (2 studies) | 2h | 2 | 4G/core | none | `scc_submit_canary_job.sh` |
| Stage D (500 studies) | 6h | 4 | 4G/core | none | `scc_submit_stage_d_job.sh` |
| EchoPrime embeddings | 4h | 4 | 4G/core | 1x A40+ | `scc_submit_echoprime_embedding_job.sh` |
| Phase 5 training | 12h | 8 | 8G/core | 1x A100-80G | TBD |

## Monitoring jobs

```bash
# Check job status
qstat -u pkarim

# Check completed job details (exit code, runtime, memory)
qacct -j <job_id>

# Read job log (while running or after)
tail -f /restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/scc_jobs/<job_name>.o<job_id>

# Check GPU availability
qgpus -v -s
```

## Common mistakes to avoid

1. **Don't run heavy Python on the login node** — use `qsub`. Even DICOM audit (38K files) exceeds the 15-min limit.
2. **Don't use `gcloud storage cp`** for downloads — use `gsutil -m cp -r`.
3. **Don't request GPU without CC/memory** — you'll get a V100 and hit OOM or CC errors.
4. **Don't use `set -euo pipefail` before module init** — relax with `set +eu` first.
5. **Don't use `find` without `2>/dev/null`** on download directories — transient `.gstmp` files cause errors.
6. **Don't forget `source scc_env.sh`** — many scripts auto-detect paths but the explicit env is more reliable.
7. **Don't run from `~` (home directory)** — always `cd /restricted/project/mimicecho/code/Echo_Cardio_VLM`.

## Storage layout

```
/restricted/project/mimicecho/          (200 GB, backed-up)
├── code/Echo_Cardio_VLM/               repo
├── echoprime_weights/                  model checkpoints
├── outputs/                            manifests, metrics, logs
└── (future) encoded embeddings

/restricted/projectnb/mimicecho/        (800 GB, non-backed-up)
└── echo_ai_data/
    └── cloud_cohorts/
        ├── scc_canary_2study/          canary DICOMs + derived
        └── stage_d_500study_scc/       Stage D DICOMs + derived
```
