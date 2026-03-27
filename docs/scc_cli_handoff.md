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

## 3) Current blocker (important)

SCC shell confirms:
- user is in group `mimicecho` (`id -Gn` includes it)
- but project storage paths are missing:
  - `/project/mimicecho`
  - `/projectnb/mimicecho`
  - `/restricted/project/mimicecho`
  - `/restricted/projectnb/mimicecho`
- `qstat -u pkarim` currently shows no active jobs

Interpretation:
- This is not a local-Mac issue and not a VS Code UI issue.
- It is an SCC provisioning/mount/path visibility issue.
- SCC support ticket has been opened.

## 4) Strategic decisions currently in effect

- Keep EchoPrime encoding label-agnostic.
- Keep measurements beyond LVEF exported now, integrate as downstream targets later.
- Defer full note-conditioning integration until storage path/provisioning is fixed.
- Do not run large data/training workflows on local Mac.

## 5) Immediate plan once SCC storage is fixed

1. Confirm canonical storage path from SCC support.
2. Clone/pull repo on SCC under that path.
3. Run workspace prep script.
4. Run preflight checks.
5. Run canary cohort.
6. Run Stage D/scale jobs.

## 6) SCC commands to run after support confirms paths

```bash
# 0) Basic host/path sanity
echo "host: $(hostname)"
id -Gn

# 1) Clone repo (example path; adjust if SCC gives different canonical path)
mkdir -p /restricted/project/mimicecho/code
cd /restricted/project/mimicecho/code
git clone https://github.com/JiahuiKChen/Echo_Cardio_VLM.git
cd Echo_Cardio_VLM

# 2) Prepare workspace (no local fallback)
./scripts/scc_prepare_workspace.sh \
  --project-name mimicecho \
  --billing-project mimic-iv-anesthesia \
  --setup-env true \
  --run-preflight false

# 3) Run access preflight explicitly
./scripts/preflight_data_access.sh \
  --billing-project mimic-iv-anesthesia \
  --bucket-primary mimic-iv-echo-1.0.physionet.org \
  --bucket-fallback mimic-iv-echo-0.1.physionet.org
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

