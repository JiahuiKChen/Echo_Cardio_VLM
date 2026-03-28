# Echo Cardio VLM: SCC CLI Handoff

Last updated: 2026-03-26

## 1) What this project is

Build a reproducible echocardiography AI pipeline on MIMIC-IV-ECHO with:
- cohort construction from BigQuery
- selective DICOM acquisition
- cine extraction + keyframe selection
- structured measurement export
- EchoPrime embedding baseline (initially LVEF-focused)

## 2) What is already done

- GitHub repo initialized and pushed:
  - `https://github.com/JiahuiKChen/Echo_Cardio_VLM`
- `.gitignore` hardened to avoid pushing data/artifacts:
  - ignores `outputs/`, `logs/`, `model_data/`, `*.pt`, `*.npz`, `*.dcm`, PDFs/zips, etc.
- Stage D local cohort artifacts exist (historical run):
  - selected studies/records
  - structured measurements export
  - LVEF manifest and baseline metrics
- Note-link sidecar exploration completed:
  - direct EC note_id join to `mimiciv_note` was 0 matches
  - admission/time bridge (`hadm_id`) produces many usable RR/DS links
  - sample sidecar outputs were created for manual inspection

## 3) Previous blocker (RESOLVED 2026-03-27)

SCC support (Aaron Fuegi) confirmed:
- Project storage is on the `/restricted` partition **only**.
- Paths `/restricted/project/mimicecho` (200 GB) and `/restricted/projectnb/mimicecho` (800 GB) exist and are writable.
- The `/restricted` partition is **only visible from `scc4.bu.edu`**.
- `scc1`, `scc2`, `geo` cannot see restricted paths — this was the cause of all previous path failures.
- `scc4.bu.edu` does not resolve from external DNS; use SCC OnDemand, BU VPN, or jump through scc1.
- Total free quota: 1 TB. If more is needed, LPI must purchase via BUYIN/SAAS program.

The blocker is resolved: paths exist, the issue was using the wrong login node.

## 4) Strategic decisions currently in effect

- Keep EchoPrime encoding label-agnostic.
- Keep measurements beyond LVEF exported now, integrate as downstream targets later.
- Defer full note-conditioning integration until after canary validation.
- Do not run large data/training workflows on local Mac.
- **Storage strategy:** No additional purchase for now.
  - EchoPrime encoded outputs → `/restricted/project/mimicecho` (200 GB, backed-up).
  - Raw DICOMs → `/restricted/projectnb/mimicecho` (800 GB, non-backed-up, transient).
  - Purge raw DICOMs after checksum + manifest + successful encoding.
  - Reassess only if storage constraints actually appear.

## 5) Immediate plan (storage confirmed)

1. ~~Confirm canonical storage path from SCC support.~~ **DONE** (2026-03-27)
2. Connect to scc4 via SCC OnDemand, VPN+SSH, or scc1 jump.
3. Clone/pull repo on SCC under `/restricted/project/mimicecho/code`.
4. Run `scripts/scc_canary_full_runner.sh` (handles workspace prep + canary end-to-end).
5. Review canary outputs in `outputs/diagnostics/canary_<timestamp>/`.
6. If canary passes, proceed to Stage D/scale jobs.

## 6) SCC commands to run (on scc4)

```bash
# 0) Connect to scc4 (pick one method):
#    a) SCC OnDemand: https://scc-ondemand.bu.edu → Clusters → SCC Shell Access
#    b) Jump: ssh pkarim@scc1.bu.edu && ssh scc4
#    c) VPN:  ssh pkarim@scc4.bu.edu

# 1) Verify paths are visible
hostname            # should show scc4*
ls -ld /restricted/project/mimicecho /restricted/projectnb/mimicecho
df -h /restricted/project/mimicecho /restricted/projectnb/mimicecho

# 2) Clone/update repo
mkdir -p /restricted/project/mimicecho/code
cd /restricted/project/mimicecho/code
git clone https://github.com/JiahuiKChen/Echo_Cardio_VLM.git 2>/dev/null || true
cd Echo_Cardio_VLM
git pull

# 3) Load modules
module load python3/3.10.12
module load google-cloud-sdk/455.0.0

# 4) Run full canary (single command — handles everything)
./scripts/scc_canary_full_runner.sh
```

## 7) Minimal Codex CLI usage on Linux

Use these commands on SCC:

```bash
codex login
codex --help
```

Start an interactive session in the current repo:

```bash
cd /path/to/Echo_Cardio_VLM
codex
```

If you have a previous local CLI session and want to continue:

```bash
codex resume --last
```

Notes:
- `resume` only works if the previous session history is available in that environment/account.
- A new SCC environment will usually not automatically include this desktop conversation context.

## 8) Copy/paste starter prompt for Codex CLI

Paste this into a fresh `codex` session on SCC:

```text
You are my coding agent for Echo Cardio VLM. Read docs/scc_cli_handoff.md first, then continue execution from the current blocker.

Current blocker:
- SCC group membership for mimicecho exists, but project storage paths are missing on login node.
- We are waiting for SCC support to confirm canonical mounted paths.

Your task now:
1) Verify current host, group, and storage path visibility.
2) If paths exist, prepare workspace with scripts/scc_prepare_workspace.sh and run preflight.
3) If paths still missing, produce a concise diagnostics report (commands + outputs + interpretation) for support follow-up.
4) Do not use local/home fallback paths for project data.
5) Keep runs reproducible and save outputs under project storage only.
```

## 9) What not to do

- Do not store MIMIC data under local Mac paths for main experiments.
- Do not push patient-level data or outputs to GitHub.
- Do not start large Stage D reruns until SCC storage path is confirmed and writable.

