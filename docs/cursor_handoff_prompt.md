# Cursor Handoff (SCC Restricted Path Update)

Last updated: 2026-03-27

## Current Ground Truth

- SCC support confirmed this project is **restricted-only** for now.
- Canonical storage:
  - Backed-up: `/restricted/project/mimicecho` (200 GB)
  - Non-backed-up: `/restricted/projectnb/mimicecho` (800 GB)
- For this project, use `scc4.bu.edu` for shell access to restricted paths.
- Earlier path visibility failures on `scc1/scc2/geo` were expected under this setup.

## Operational Strategy

- Keep code, manifests, configs, summaries, metrics, and small derived artifacts in:
  - `/restricted/project/mimicecho`
- Keep raw DICOM and bulky transient preprocessing files in:
  - `/restricted/projectnb/mimicecho`
- Stay under quota by deleting raw DICOM/transient artifacts after:
  1. checksum/integrity checks,
  2. manifest creation,
  3. successful encoding export.

## Immediate Validation Steps

```bash
hostname
ls -ld /restricted/project/mimicecho /restricted/projectnb/mimicecho
df -h /restricted/project/mimicecho /restricted/projectnb/mimicecho
```

If these pass, proceed:

```bash
cd /restricted/project/mimicecho/code
git clone https://github.com/JiahuiKChen/Echo_Cardio_VLM.git || true
cd Echo_Cardio_VLM
git pull

module load python3/3.10.12
module load google-cloud-sdk/455.0.0

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

