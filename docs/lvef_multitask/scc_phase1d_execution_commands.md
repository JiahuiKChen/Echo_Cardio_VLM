# SCC Phase 1D isolated execution commands

This is the authoritative Phase 1D SCC execution workflow. It runs only non-model portability, duplicate-provenance, canonical-clip inventory, and clinical-metadata audits. It does not fit or tune models, read predictions or confirmatory performance, generate embeddings, re-extract or download DICOMs, or modify any historical artifact.

The prior clinical-metadata failure was an interpreter-selection failure: the Phase 1C document used bare `python3`, bypassed the runbook's validated interpreter, and selected an older SCC system Python that raised `SyntaxError` at `from __future__ import annotations`. The future import is valid under the required project runtime and remains unchanged. This workflow never falls back to a system interpreter. It selects the preferred project environment or the explicit `LVEF_SCC_PYTHON` override, requires Python 3.10 or newer, validates NumPy, pandas, SciPy, scikit-learn, and PyYAML, and records versions plus the executable SHA-256 when readable.

Each marked audit body below is extracted into a temporary restricted script, checked with `/bin/bash -n`, and executed by `/bin/bash` under a clean allowlisted environment. Audit stdout and stderr remain in restricted logs. The dispatcher validates the interpreter in a disposable temporary directory before it creates the Phase 1D packet. It then moves the safe validation record into the new packet. The dispatcher captures each child status inside an `if`, so a nonzero audit cannot trigger a parent shell's `errexit`; the nonzero value remains visible in the aggregate runner record and in `PHASE1D_AUDIT_STATUS`.

## Dispatcher to paste into the interactive SCC shell

Set `LVEF_PHASE1D_EXPECTED_COMMIT` to the exact pushed commit reported with the Phase 1D handoff. `LVEF_SCC_PYTHON` is optional; when unset, the resolver tests `/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python`. The generated environment file makes both inputs explicit; the audit bodies do not depend on other parent-shell variables.

```bash
lvef_phase1d_dispatch() {
  local worktree branch document run_stamp run_id phase1d_root env_file
  local selected_python resolved_python preflight_temp preflight_record
  local resolver_status first_nonzero

  worktree="/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
  branch="codex/lvef-multitask-revalidation"
  document="docs/lvef_multitask/scc_phase1d_execution_commands.md"

  if [[ ! "${LVEF_PHASE1D_EXPECTED_COMMIT:-}" =~ ^[0-9a-f]{40}$ ]]; then
    printf '%s\n' 'phase1d_dispatch_status=INVALID_OR_MISSING_EXPECTED_COMMIT'
    return 64
  fi
  selected_python="${LVEF_SCC_PYTHON:-/restricted/project/mimicecho/code/Echo_Cardio_VLM/.venv-echoprime/bin/python}"
  case "$selected_python" in
    /*) ;;
    *)
      printf '%s\n' 'phase1d_dispatch_status=PYTHON_OVERRIDE_MUST_BE_ABSOLUTE'
      return 64
      ;;
  esac

  cd "$worktree" || return 69
  if [[ "$(git branch --show-current)" != "$branch" ]] || \
     [[ "$(git rev-parse HEAD)" != "$LVEF_PHASE1D_EXPECTED_COMMIT" ]] || \
     [[ -n "$(git status --porcelain)" ]]; then
    printf '%s\n' 'phase1d_dispatch_status=GIT_AUTHORITY_MISMATCH'
    return 65
  fi

  preflight_temp="$(mktemp -d "${TMPDIR:-/tmp}/lvef_phase1d_python_preflight.XXXXXX")" || return 73
  preflight_record="$preflight_temp/phase1d_preflight_python_environment.json"
  umask 077
  if resolved_python="$(
    LVEF_SCC_PYTHON="$selected_python" \
      scripts/resolve_lvef_scc_python.sh --record-json "$preflight_record"
  )"; then
    :
  else
    resolver_status=$?
    rm -f "$preflight_record"
    rmdir "$preflight_temp" 2>/dev/null || true
    printf 'phase1d_dispatch_status=PYTHON_PREFLIGHT_FAILED\n'
    return "$resolver_status"
  fi

  run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  run_id="${run_stamp}_${LVEF_PHASE1D_EXPECTED_COMMIT:0:12}"
  phase1d_root="/restricted/project/mimicecho/audits/lvef_multitask_phase1d_${run_id}"
  if [[ -e "$phase1d_root" ]]; then
    rm -f "$preflight_record"
    rmdir "$preflight_temp" 2>/dev/null || true
    printf '%s\n' 'phase1d_dispatch_status=REFUSED_EXISTING_PHASE1D_ROOT'
    return 73
  fi
  if ! mkdir -p "$phase1d_root/aggregate" "$phase1d_root/restricted"; then
    rm -f "$preflight_record"
    rmdir "$preflight_temp" 2>/dev/null || true
    return 73
  fi
  if ! mv \
    "$preflight_record" \
    "$phase1d_root/aggregate/phase1d_preflight_python_environment.json"; then
    printf '%s\n' 'phase1d_dispatch_status=PYTHON_RECORD_FINALIZATION_FAILED'
    return 73
  fi
  rmdir "$preflight_temp" 2>/dev/null || true
  env_file="$phase1d_root/restricted/runner.env"
  printf 'LVEF_PHASE1D_EXPECTED_COMMIT=%s\n' "$LVEF_PHASE1D_EXPECTED_COMMIT" >"$env_file"
  printf 'LVEF_SCC_PYTHON=%s\n' "$resolved_python" >>"$env_file"
  chmod 600 "$env_file"

  run_phase1d_block() {
    local block_id="$1"
    local label="$2"
    local child_status
    shift 2
    local runner_args=(
      --document "$document"
      --block-id "$block_id"
      --run-dir "$phase1d_root"
      --label "$label"
      --env-file "$env_file"
    )
    while [[ "$#" -gt 0 ]]; do
      runner_args+=(--safe-output "$1")
      shift
    done
    if scripts/run_lvef_scc_bash_block.sh "${runner_args[@]}"; then
      child_status=0
    else
      child_status=$?
    fi
    printf 'phase1d_block_status\t%s\t%s\n' "$label" "$child_status"
    LAST_PHASE1D_BLOCK_STATUS="$child_status"
  }

  first_nonzero=0
  run_phase1d_block \
    phase1d-portability-preflight \
    phase1d_portability_preflight \
    aggregate/phase1d_preflight_python_environment.json
  if [[ "$LAST_PHASE1D_BLOCK_STATUS" -ne 0 ]]; then
    first_nonzero="$LAST_PHASE1D_BLOCK_STATUS"
    printf 'phase1d_dispatch_status=SKIPPED_AUDITS_AFTER_FAILED_PREFLIGHT\n'
  else
    run_phase1d_block \
      phase1d-duplicate-provenance-v2 \
      phase1d_duplicate_provenance_v2 \
      aggregate/duplicate_v2_python_environment.json \
      aggregate/duplicate_v2/duplicate_clip_evidence_availability.summary.json \
      aggregate/duplicate_v2/duplicate_clip_evidence_availability_counts.csv \
      aggregate/duplicate_v2/duplicate_clip_adjudication_v2.summary.json \
      aggregate/duplicate_v2/duplicate_clip_adjudication_v2_reason_counts.csv \
      aggregate/duplicate_v2/duplicate_clip_adjudication_v2_safety_gate.json
    if [[ "$LAST_PHASE1D_BLOCK_STATUS" -ne 0 && "$first_nonzero" -eq 0 ]]; then
      first_nonzero="$LAST_PHASE1D_BLOCK_STATUS"
    fi

    run_phase1d_block \
      phase1d-canonical-clip-inventory \
      phase1d_canonical_clip_inventory \
      aggregate/canonical_inventory_python_environment.json \
      aggregate/canonical_inventory/canonical_selected_clip_inventory.summary.json \
      aggregate/canonical_inventory/canonical_selected_clip_inventory_by_component.csv \
      aggregate/canonical_inventory/canonical_selected_clip_inventory_safety_gate.json
    if [[ "$LAST_PHASE1D_BLOCK_STATUS" -ne 0 && "$first_nonzero" -eq 0 ]]; then
      first_nonzero="$LAST_PHASE1D_BLOCK_STATUS"
    fi

    run_phase1d_block \
      phase1d-clinical-metadata \
      phase1d_clinical_metadata \
      aggregate/clinical_metadata_python_environment.json \
      aggregate/clinical_metadata/clinical_metadata_review_packet_manifest.json \
      aggregate/clinical_metadata/clinical_metadata_schema_summary.json \
      aggregate/clinical_metadata/clinical_metadata_ambiguity_counts.csv \
      aggregate/clinical_metadata/clinical_metadata_unit_summary.csv \
      aggregate/clinical_metadata/clinical_metadata_alias_summary.csv \
      aggregate/clinical_metadata/clinical_metadata_safety_gate.json \
      aggregate/openevidence_followup/openevidence_targeted_followup_prompt_generated.md \
      aggregate/openevidence_followup/openevidence_targeted_followup_safety_gate.json
    if [[ "$LAST_PHASE1D_BLOCK_STATUS" -ne 0 && "$first_nonzero" -eq 0 ]]; then
      first_nonzero="$LAST_PHASE1D_BLOCK_STATUS"
    fi
  fi

  local safe_relative
  for safe_relative in \
    aggregate/phase1d_preflight_python_environment.json \
    aggregate/phase1d_portability_preflight.runner_status.json \
    aggregate/duplicate_v2_python_environment.json \
    aggregate/phase1d_duplicate_provenance_v2.runner_status.json \
    aggregate/duplicate_v2/duplicate_clip_evidence_availability.summary.json \
    aggregate/duplicate_v2/duplicate_clip_evidence_availability_counts.csv \
    aggregate/duplicate_v2/duplicate_clip_adjudication_v2.summary.json \
    aggregate/duplicate_v2/duplicate_clip_adjudication_v2_reason_counts.csv \
    aggregate/duplicate_v2/duplicate_clip_adjudication_v2_safety_gate.json \
    aggregate/canonical_inventory_python_environment.json \
    aggregate/phase1d_canonical_clip_inventory.runner_status.json \
    aggregate/canonical_inventory/canonical_selected_clip_inventory.summary.json \
    aggregate/canonical_inventory/canonical_selected_clip_inventory_by_component.csv \
    aggregate/canonical_inventory/canonical_selected_clip_inventory_safety_gate.json \
    aggregate/clinical_metadata_python_environment.json \
    aggregate/phase1d_clinical_metadata.runner_status.json \
    aggregate/clinical_metadata/clinical_metadata_review_packet_manifest.json \
    aggregate/clinical_metadata/clinical_metadata_schema_summary.json \
    aggregate/clinical_metadata/clinical_metadata_ambiguity_counts.csv \
    aggregate/clinical_metadata/clinical_metadata_unit_summary.csv \
    aggregate/clinical_metadata/clinical_metadata_alias_summary.csv \
    aggregate/clinical_metadata/clinical_metadata_safety_gate.json \
    aggregate/openevidence_followup/openevidence_targeted_followup_prompt_generated.md \
    aggregate/openevidence_followup/openevidence_targeted_followup_safety_gate.json; do
    if [[ -f "$phase1d_root/$safe_relative" ]]; then
      printf 'phase1d_safe_output_ready=%s\n' "$phase1d_root/$safe_relative"
    fi
  done
  printf 'phase1d_audit_root=%s\n' "$phase1d_root"
  printf 'phase1d_overall_status=%s\n' "$first_nonzero"
  PHASE1D_LAST_ROOT="$phase1d_root"
  return "$first_nonzero"
}

if lvef_phase1d_dispatch; then
  PHASE1D_AUDIT_STATUS=0
else
  PHASE1D_AUDIT_STATUS=$?
fi
printf 'phase1d_parent_shell_survived=true\n'
printf 'phase1d_captured_status=%s\n' "$PHASE1D_AUDIT_STATUS"
unset -f lvef_phase1d_dispatch run_phase1d_block
```

The final two lines are printed even when an audit fails. Do not convert the dispatcher to `source`, `eval`, or an unguarded runner call. A nonzero `PHASE1D_AUDIT_STATUS` is a real failure or scientific blocker and must not be treated as success merely because the parent shell survived.

## Committed audit bodies extracted by the runner

These blocks are implementation bodies. Do not paste or source them directly.

<!-- lvef-scc-block:phase1d-portability-preflight -->
```bash
set -euo pipefail

WORKTREE="/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$LVEF_PHASE1D_EXPECTED_COMMIT"
test -z "$(git status --porcelain)"

test -f "$LVEF_SCC_RUN_DIR/aggregate/phase1d_preflight_python_environment.json"
LVEF_SCC_PYTHON_RESOLVED="$LVEF_SCC_PYTHON"
test -x "$LVEF_SCC_PYTHON_RESOLVED"
readonly LVEF_SCC_PYTHON_RESOLVED
"$LVEF_SCC_PYTHON_RESOLVED" scripts/run_phase1a_tests.py
```

<!-- lvef-scc-block:phase1d-duplicate-provenance-v2 -->
```bash
set -euo pipefail

WORKTREE="/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
FULLSCALE_ROOT="/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all"
STAGE_D_ROOT="/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/stage_d_500study_scc"
cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$LVEF_PHASE1D_EXPECTED_COMMIT"
test -z "$(git status --porcelain)"

LVEF_SCC_PYTHON_RESOLVED="$(
  scripts/resolve_lvef_scc_python.sh \
    --record-json "$LVEF_SCC_RUN_DIR/aggregate/duplicate_v2_python_environment.json"
)"
readonly LVEF_SCC_PYTHON_RESOLVED

SELECTED_STUDIES="$FULLSCALE_ROOT/manifests/all_eligible_studies.csv"
MERGED_CLIP_MANIFEST="$FULLSCALE_ROOT/merged_clip_embeddings_512/clip_embedding_manifest.csv"
MERGED_CLIP_NPZ="$FULLSCALE_ROOT/merged_clip_embeddings_512/clip_embeddings_512.npz"

COMPONENT_NAMES=(stage_d)
COMPONENT_MANIFESTS=("$STAGE_D_ROOT/echoprime_embeddings_512/clip_embedding_manifest.csv")
COMPONENT_NPZS=("$STAGE_D_ROOT/echoprime_embeddings_512/clip_embeddings_512.npz")
COMPONENT_EXTRACTIONS=("$STAGE_D_ROOT/extract_allclip/extraction_manifest.csv")
COMPONENT_DICOM_AUDITS=("$STAGE_D_ROOT/audit/dicom_audit.csv")
for index in {0..8}; do
  printf -v component 'batch_%03d' "$index"
  COMPONENT_NAMES+=("$component")
  COMPONENT_MANIFESTS+=("$FULLSCALE_ROOT/batches/${component}_embeddings/clip_embedding_manifest.csv")
  COMPONENT_NPZS+=("$FULLSCALE_ROOT/batches/${component}_embeddings/clip_embeddings_512.npz")
  COMPONENT_EXTRACTIONS+=("$FULLSCALE_ROOT/batches/${component}_extraction_manifest.csv")
  COMPONENT_DICOM_AUDITS+=("$FULLSCALE_ROOT/batches/${component}_audit/dicom_audit.csv")
done
test "${#COMPONENT_NAMES[@]}" -eq 10

COMPONENT_ARGS=()
for index in "${!COMPONENT_NAMES[@]}"; do
  test -f "${COMPONENT_MANIFESTS[$index]}"
  test -f "${COMPONENT_NPZS[$index]}"
  test -f "${COMPONENT_EXTRACTIONS[$index]}"
  test -f "${COMPONENT_DICOM_AUDITS[$index]}"
  COMPONENT_ARGS+=(--component-manifest "${COMPONENT_NAMES[$index]}=${COMPONENT_MANIFESTS[$index]}")
  COMPONENT_ARGS+=(--component-embedding-npz "${COMPONENT_NAMES[$index]}=${COMPONENT_NPZS[$index]}")
  COMPONENT_ARGS+=(--component-extraction-manifest "${COMPONENT_NAMES[$index]}=${COMPONENT_EXTRACTIONS[$index]}")
  COMPONENT_ARGS+=(--component-dicom-audit "${COMPONENT_NAMES[$index]}=${COMPONENT_DICOM_AUDITS[$index]}")
done
test -f "$SELECTED_STUDIES"
test -f "$MERGED_CLIP_MANIFEST"
test -f "$MERGED_CLIP_NPZ"

mkdir -p \
  "$LVEF_SCC_RUN_DIR/aggregate/duplicate_v2" \
  "$LVEF_SCC_RUN_DIR/restricted/duplicate_v2"
"$LVEF_SCC_PYTHON_RESOLVED" scripts/audit_duplicate_clip_keys_v2.py \
  "${COMPONENT_ARGS[@]}" \
  --merged-manifest "$MERGED_CLIP_MANIFEST" \
  --merged-embedding-npz "$MERGED_CLIP_NPZ" \
  --selected-studies "$SELECTED_STUDIES" \
  --aggregate-output-dir "$LVEF_SCC_RUN_DIR/aggregate/duplicate_v2" \
  --restricted-output-dir "$LVEF_SCC_RUN_DIR/restricted/duplicate_v2" \
  --expected-duplicate-keys 32 \
  --expected-embedding-dim 512 \
  --vector-rtol 1e-6 \
  --vector-atol 1e-6
```

<!-- lvef-scc-block:phase1d-canonical-clip-inventory -->
```bash
set -euo pipefail

WORKTREE="/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
FULLSCALE_ROOT="/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all"
STAGE_D_ROOT="/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/stage_d_500study_scc"
cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$LVEF_PHASE1D_EXPECTED_COMMIT"
test -z "$(git status --porcelain)"

LVEF_SCC_PYTHON_RESOLVED="$(
  scripts/resolve_lvef_scc_python.sh \
    --record-json "$LVEF_SCC_RUN_DIR/aggregate/canonical_inventory_python_environment.json"
)"
readonly LVEF_SCC_PYTHON_RESOLVED

SELECTED_STUDIES="$FULLSCALE_ROOT/manifests/all_eligible_studies.csv"
COMPONENT_NAMES=(stage_d)
COMPONENT_MANIFESTS=("$STAGE_D_ROOT/echoprime_embeddings_512/clip_embedding_manifest.csv")
COMPONENT_EXTRACTIONS=("$STAGE_D_ROOT/extract_allclip/extraction_manifest.csv")
COMPONENT_DICOM_AUDITS=("$STAGE_D_ROOT/audit/dicom_audit.csv")
for index in {0..8}; do
  printf -v component 'batch_%03d' "$index"
  COMPONENT_NAMES+=("$component")
  COMPONENT_MANIFESTS+=("$FULLSCALE_ROOT/batches/${component}_embeddings/clip_embedding_manifest.csv")
  COMPONENT_EXTRACTIONS+=("$FULLSCALE_ROOT/batches/${component}_extraction_manifest.csv")
  COMPONENT_DICOM_AUDITS+=("$FULLSCALE_ROOT/batches/${component}_audit/dicom_audit.csv")
done
test "${#COMPONENT_NAMES[@]}" -eq 10

COMPONENT_ARGS=()
for index in "${!COMPONENT_NAMES[@]}"; do
  test -f "${COMPONENT_MANIFESTS[$index]}"
  test -f "${COMPONENT_EXTRACTIONS[$index]}"
  test -f "${COMPONENT_DICOM_AUDITS[$index]}"
  COMPONENT_ARGS+=(--component-manifest "${COMPONENT_NAMES[$index]}=${COMPONENT_MANIFESTS[$index]}")
  COMPONENT_ARGS+=(--component-extraction-manifest "${COMPONENT_NAMES[$index]}=${COMPONENT_EXTRACTIONS[$index]}")
  COMPONENT_ARGS+=(--component-dicom-audit "${COMPONENT_NAMES[$index]}=${COMPONENT_DICOM_AUDITS[$index]}")
done
test -f "$SELECTED_STUDIES"

mkdir -p \
  "$LVEF_SCC_RUN_DIR/aggregate/canonical_inventory" \
  "$LVEF_SCC_RUN_DIR/restricted/canonical_inventory"
"$LVEF_SCC_PYTHON_RESOLVED" scripts/audit_canonical_selected_clip_inventory.py \
  "${COMPONENT_ARGS[@]}" \
  --selected-studies "$SELECTED_STUDIES" \
  --hash-mode duplicates \
  --expected-selected-imaging-studies 4525 \
  --aggregate-output-dir "$LVEF_SCC_RUN_DIR/aggregate/canonical_inventory" \
  --restricted-output-dir "$LVEF_SCC_RUN_DIR/restricted/canonical_inventory"
```

<!-- lvef-scc-block:phase1d-clinical-metadata -->
```bash
set -euo pipefail

WORKTREE="/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
FULLSCALE_ROOT="/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all"
cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$LVEF_PHASE1D_EXPECTED_COMMIT"
test -z "$(git status --porcelain)"

LVEF_SCC_PYTHON_RESOLVED="$(
  scripts/resolve_lvef_scc_python.sh \
    --record-json "$LVEF_SCC_RUN_DIR/aggregate/clinical_metadata_python_environment.json"
)"
readonly LVEF_SCC_PYTHON_RESOLVED

RAW_CANONICAL_MAPPING="$FULLSCALE_ROOT/measurement_registry_v1/measurement_to_canonical_mapping.csv"
test -f "$RAW_CANONICAL_MAPPING"
mkdir -p \
  "$LVEF_SCC_RUN_DIR/aggregate/clinical_metadata" \
  "$LVEF_SCC_RUN_DIR/aggregate/openevidence_followup" \
  "$LVEF_SCC_RUN_DIR/restricted/clinical_metadata"
"$LVEF_SCC_PYTHON_RESOLVED" scripts/lvef_multitask_clinical_metadata.py \
  --mapping-csv "$RAW_CANONICAL_MAPPING" \
  --aggregate-output-dir "$LVEF_SCC_RUN_DIR/aggregate/clinical_metadata" \
  --restricted-output-dir "$LVEF_SCC_RUN_DIR/restricted/clinical_metadata" \
  --followup-output-dir "$LVEF_SCC_RUN_DIR/aggregate/openevidence_followup"
```

## Aggregate-safe outputs

The dispatcher and runner print only runner status JSON and paths from the explicit allowlists above. After each file's own safety gate reports `PASS`, the following may be pasted back:

- the four `*_python_environment.json` records;
- the five files beneath `aggregate/duplicate_v2/`;
- the three files beneath `aggregate/canonical_inventory/`;
- the six files beneath `aggregate/clinical_metadata/`;
- the generated prompt and safety gate beneath `aggregate/openevidence_followup/`;
- the four `*.runner_status.json` records.

The interpreter records contain only environment provenance: executable path and hash, Python version, package versions/import flags, override flag, and no-fallback flag. They contain no patient, study, clip, DICOM, label, prediction, or embedding data.

## Restricted outputs that must not be pasted

Do not paste any file beneath the Phase 1D `restricted/` tree, including:

- runner environment and extracted-block logs;
- audit stdout/stderr and syntax logs;
- per-group duplicate evidence, adjudication, or resolution rows;
- canonical clip locator/source inventory rows;
- raw-name, description, unit, alias, ambiguity-detail, literature-candidate, or clinician-questionnaire rows.

Do not paste identifiers, clip keys, locators, per-file hashes tied to DICOM/NPZ sources, embeddings, labels, or patient-level values even if an audit fails before its aggregate safety gate.
