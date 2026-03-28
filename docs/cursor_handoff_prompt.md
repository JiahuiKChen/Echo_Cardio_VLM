# Cursor Handoff (SCC Restricted Path Update)

Last updated: 2026-03-27

## Current Ground Truth (confirmed by SCC support ticket, 2026-03-27)

- SCC support (Aaron Fuegi) confirmed this project is **restricted-only**.
- Canonical storage:
  - Backed-up: `/restricted/project/mimicecho` (200 GB)
  - Non-backed-up: `/restricted/projectnb/mimicecho` (800 GB)
  - Total free baseline quota: **1 TB** (maximum free allowance).
- The `/restricted` partition is **only accessible from `scc4.bu.edu`**.
  - `scc1`, `scc2`, and `geo` **cannot** see these paths — this is expected, not a bug.
- `scc4.bu.edu` does not resolve from external/public DNS.
  - **Preferred access:** SCC OnDemand web interface (https://scc-ondemand.bu.edu)
  - **Alternative:** SSH to scc1 first, then `ssh scc4` from there.
  - **Alternative:** BU VPN then `ssh pkarim@scc4.bu.edu`.
- If runs exceed 1 TB, the LPI must purchase additional space via BU's
  BUYIN or SAAS programs:
  https://www.bu.edu/tech/support/research/computing-resources/file-storage/proj-diskspace/#BUYINandSAAS

## Operational Strategy

### Storage layout
- `/restricted/project/mimicecho` (200 GB, backed-up):
  - Code, manifests, configs, summaries, metrics
  - **EchoPrime encoded outputs** (embeddings, encoded representations)
  - Small derived artifacts (keyframes, LVEF manifests, split maps)
- `/restricted/projectnb/mimicecho` (800 GB, non-backed-up):
  - Raw DICOM downloads (transient)
  - Bulky intermediate preprocessing files

### Storage discipline (stay within 1 TB free quota)
- No additional storage purchase for now — adjust only if constraints appear.
- Delete raw DICOMs from `/restricted/projectnb` after:
  1. checksum/integrity verification,
  2. manifest creation,
  3. successful EchoPrime encoding export to `/restricted/project`.
- This encode-then-purge cycle should keep usage well within limits.
- Monitor usage with `df -h` before and after each batch.

## How to Connect to scc4

`scc4.bu.edu` does **not** resolve from external/public DNS. Three options:

### Option A: SCC OnDemand (recommended by SCC support)
1. Go to https://scc-ondemand.bu.edu
2. Log in with BU credentials + Duo
3. Open a terminal (Clusters → SCC Shell Access)
4. From the shell, the restricted paths should be visible

### Option B: Jump through scc1
```bash
ssh pkarim@scc1.bu.edu   # Duo 2FA
ssh scc4                  # internal hop, no 2FA
```

### Option C: BU VPN + direct SSH
```bash
# Connect to BU VPN first, then:
ssh pkarim@scc4.bu.edu
```

## Immediate Validation Steps (run on scc4)

```bash
hostname
ls -ld /restricted/project/mimicecho /restricted/projectnb/mimicecho
df -h /restricted/project/mimicecho /restricted/projectnb/mimicecho
```

If these pass, run the full canary with a single command:

```bash
cd /restricted/project/mimicecho/code
git clone https://github.com/JiahuiKChen/Echo_Cardio_VLM.git || true
cd Echo_Cardio_VLM
git pull

module load python3/3.10.12
module load google-cloud-sdk/455.0.0

./scripts/scc_canary_full_runner.sh
```

Or step by step:

```bash
./scripts/scc_prepare_workspace.sh \
  --project-name mimicecho \
  --billing-project mimic-iv-anesthesia \
  --setup-env true \
  --run-preflight false

source scc_env.sh
./scripts/scc_run_canary_2study.sh --billing-project mimic-iv-anesthesia
```

## Pass Criteria (Canary)

- Cohort selection succeeds.
- DICOM download succeeds into `/restricted/projectnb/mimicecho/...`.
- Extraction/postprocess runs without local-home fallback.
- Outputs and manifests land under `/restricted/project/mimicecho/...`.

## Known Modeling Context

- Structured measurements beyond LVEF are already exported and preserved.
- Current EchoPrime baseline is LVEF-specific (labels: `lvef`, `lvef_binary_reduced`).
- Keep encoding label-agnostic; add non-LVEF targets in later downstream experiments.
- Notes/text conditioning is deferred for now (not blocking canary/runway setup).

## Copy/Paste Prompt for Cursor

Use this prompt in Cursor:

```text
You are my coding agent for Echo Cardio VLM. Start by reading docs/cursor_handoff_prompt.md and docs/scc_cli_handoff.md.

Critical constraints:
- SCC storage is restricted-only and currently canonical paths are:
  /restricted/project/mimicecho and /restricted/projectnb/mimicecho
- Use scc4 context for this project.
- Do not use local/home fallback paths for project data.
- Keep raw DICOM in /restricted/projectnb and reproducibility artifacts in /restricted/project.

Task sequence:
1) Verify host/path/quota visibility for the restricted paths.
2) If paths are visible, run:
   - scripts/scc_prepare_workspace.sh
   - source scc_env.sh
   - scripts/scc_run_canary_2study.sh
3) Collect and summarize outputs:
   - selection summary
   - extraction manifest summary
   - structured_measurements summary
4) If any step fails, produce a concise diagnostics report with:
   - exact failing command
   - stderr/stdout
   - root-cause hypothesis
   - minimum fix and retry command
5) Preserve reproducibility: save commands, timestamps, and output paths.

Do not start large Stage D runs until the 2-study canary passes end-to-end.
```

