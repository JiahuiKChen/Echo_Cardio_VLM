# Secure analysis bridge

Status: **two explicit modes implemented; direct SCC access institutionally authorized; export remains separately reviewed and fail closed**

The bridge separates permission to inspect restricted SCC data from permission to place an artifact in Git or a manuscript workspace. Direct analysis is no longer constrained to obfuscated schemas. It also does not make its outputs exportable.

| Mode | Permitted | Output boundary | Authority it does not grant |
|---|---|---|---|
| `APPROVED_DIRECT_RESTRICTED_AGENT` | An approved Codex/API agent may read exact restricted schemas, values, identifiers, paths, DICOM content, arrays, embeddings, predictions, discrepancies, and detailed logs when needed for the authorized task. | Inputs, detailed findings, receipts, stdout/stderr, and row-level outputs remain below approved SCC roots. Ordinary responses use the minimum identifying detail needed. | No Git/manuscript export, confirmatory-performance access, model fitting, or full C3 execution. |
| `MANUSCRIPT_OR_GIT_EXPORT` | A prespecified aggregate JSON/CSV schema may be reviewed for release. | Only unchanged, schema-valid bytes with a hash-bound request, separate completed human approval, release receipt, and passing staged-Git gate may enter the allowlisted repository export root. | Direct-agent authorization alone cannot release an artifact. |

The executable policy is [`configs/lvef_multitask_safe_export_policy.yaml`](../../configs/lvef_multitask_safe_export_policy.yaml). The existing aggregate writers and their prohibited-column/key checks remain unchanged; this bridge adds an outer authority and release layer.

## Fail-closed path contract

The approved direct and staging roots are:

- `/restricted/project/mimicecho`
- `/restricted/projectnb/mimicecho`

Every supplied path must bind to exactly one approved root. The implementation rejects `..`, a missing required input, special files, root ambiguity, symlinked files, symlinked descendants, any resolved escape from the approved root, and any restricted receipt/candidate/review-manifest path inside the repository. Controlled receipts and review manifests are created exclusively and are never silently overwritten. The repository release destination is restricted to `docs/lvef_multitask/approved_exports/`; a destination or receipt that already exists blocks release.

These software checks complement, rather than replace, SCC ACLs and the DUA. A bind mount that presents an approved lexical path should be documented by the filesystem preflight before it is relied upon; the path checker still requires the resolved target to remain within the configured approved-root authority.

## Direct restricted-agent receipt

Before a direct restricted audit, record its bounded purpose, source commit, approved organization class, inputs, intended outputs, and optional command checksum in a restricted receipt. For example, from the dedicated SCC worktree:

```bash
PYTHON_BIN=/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python
REPO=/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask
RUN_ROOT=/restricted/projectnb/mimicecho/audits/lvef_multitask_phase1ebc

"$PYTHON_BIN" "$REPO/scripts/lvef_multitask_analysis_modes.py" \
  --policy "$REPO/configs/lvef_multitask_safe_export_policy.yaml" \
  direct-receipt \
  --receipt "$RUN_ROOT/restricted/authorized_agent_receipt.json" \
  --purpose "Phase 1E-B/C source, storage, and technical metadata preflight" \
  --source-commit "$(git -C "$REPO" rev-parse HEAD)" \
  --organization-class "institutionally approved organizational workspace/API" \
  --input "$RUN_ROOT/restricted/source/reconstruction_source_manifest.csv" \
  --output "$RUN_ROOT/restricted/phase1ebc"
```

The receipt may contain restricted relative locators and therefore stays on SCC. It explicitly records `export_authority_granted: false` and `confirmatory_performance_authority_granted: false`. A failed receipt command prints only an error class, not a sensitive path or row.

## Two-step reviewed export

### 1. Prepare and bind the candidate

The direct analysis first writes a candidate and its native aggregate safety gate below a restricted staging root. The preparation command revalidates one named schema profile, computes the candidate and policy SHA-256 values, and exclusively writes an export request plus a pending approval template:

```bash
"$PYTHON_BIN" "$REPO/scripts/prepare_lvef_safe_export.py" \
  --policy "$REPO/configs/lvef_multitask_safe_export_policy.yaml" \
  --candidate "$RUN_ROOT/aggregate/c3_full_source_preflight.summary.json" \
  --profile c3_full_source_preflight_summary_json \
  --request "$RUN_ROOT/restricted/export_review/source_summary.request.json" \
  --approval-template "$RUN_ROOT/restricted/export_review/source_summary.approval.json" \
  --purpose "Git-safe aggregate C3 source-preflight summary"
```

Preparation is not release. The generated template has `decision: PENDING` and the request has `release_authority_granted: false`.

### 2. Human review and approval

An authorized human reviewer inspects the exact candidate and its aggregate safety evidence on SCC. If approved, the reviewer changes only the restricted approval manifest to `decision: APPROVED` and records reviewer identity, role, a timezone-qualified approval timestamp, and rationale. The request ID, candidate SHA-256, profile, policy ID, and policy SHA-256 must remain unchanged. The detailed approval manifest and reviewer identity remain restricted.

### 3. Revalidate and release unchanged bytes

The release command checks both restricted manifests, recomputes every binding, reruns the schema and restricted-value checks on the current candidate, and refuses overwrite:

```bash
"$PYTHON_BIN" "$REPO/scripts/release_lvef_safe_export.py" \
  --policy "$REPO/configs/lvef_multitask_safe_export_policy.yaml" \
  --candidate "$RUN_ROOT/aggregate/c3_full_source_preflight.summary.json" \
  --request "$RUN_ROOT/restricted/export_review/source_summary.request.json" \
  --approval "$RUN_ROOT/restricted/export_review/source_summary.approval.json" \
  --destination docs/lvef_multitask/approved_exports/c3_full_source_preflight.summary.json
```

The Git-safe sidecar records the request, candidate, policy and profile hashes; reviewer role and approval date; destination; and safety status. It deliberately omits the restricted source locator and reviewer identity.

### 4. Gate the staged Git index

After intentionally staging repository changes, run:

```bash
"$PYTHON_BIN" "$REPO/scripts/check_lvef_git_export_safety.py" \
  --policy "$REPO/configs/lvef_multitask_safe_export_policy.yaml" \
  --repo "$REPO"
```

The gate reads staged blobs, not merely working-tree files. It rejects symlinks/submodules, patient-level table headers or JSON keys, forbidden restricted-value patterns, DICOM/array/model/archive suffixes, binary content, an artifact under the approved export root without a receipt, an orphan receipt, an old-policy receipt, or staged bytes that differ from the approved candidate. Normal executable source scripts are permitted; reviewed export artifacts must be non-executable regular files.

## Current export profiles

The v1 policy contains closed JSON/CSV profiles for the full-source summary, production-batch table, GCS cost estimate, storage projection, aggregate safety gate, and Git-safe clinical-adjudication summary. Extra top-level JSON keys and extra CSV columns fail. Raw descriptions, aliases, identifiers, locators, paths, embeddings, labels, predictions, and patient/study rows are prohibited.

Figures are not silently treated as safe binary exports in v1. If a figure is needed later, a specific figure profile, metadata stripping/inspection routine, synthetic tests, and independent review must be added before release. Renaming an unsupported binary does not pass because binary bytes are rejected.

## Operational boundaries

- Direct analysis may inspect a prediction or performance artifact only when a separately authorized scientific phase permits that access. The institutional AI approval does not open the confirmatory test set.
- A schema-only inspector may still be selected when it is scientifically sufficient, but it is an optional least-data diagnostic rather than an access prerequisite.
- Direct receipts, export requests, approval manifests, discrepancy rows, and detailed failures remain restricted even when the final aggregate candidate passes.
- Any candidate mutation, profile change, or policy change after approval requires a new preparation and approval cycle.
- The staged-Git check is mandatory but not a substitute for a human `git diff --cached` review and the project-specific aggregate safety gate.
