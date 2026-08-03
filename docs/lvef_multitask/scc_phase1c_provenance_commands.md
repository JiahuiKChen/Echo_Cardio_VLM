# SCC Phase 1C restricted provenance commands

These commands run only the 32-key restricted diagnostic and the aggregate exact-LVEF-40 count. They do not fit models, regenerate embeddings or predictions, calculate performance, inspect outcomes beyond the prespecified LVEF label-definition count, modify historical artifacts, or print identifiers and locators.

Run the block from the dedicated SCC worktree in the same Bash shell after sections 1–3 of `scc_revalidation_runbook.md`. Those sections define `PYTHON_BIN`, selected/structured/split/LVEF authorities, merged clip files, Stage-D clip files, and the nine batch arrays. The block creates a fresh directory and refuses reuse. Restricted detail and stderr remain on SCC.

```bash
set -euo pipefail

for required_var in \
  PYTHON_BIN \
  SELECTED_STUDIES \
  STRUCTURED_MEASUREMENTS \
  LVEF_LABELS \
  SPLIT_MAP \
  MERGED_CLIP_NPZ \
  MERGED_CLIP_MANIFEST \
  STAGE_D_CLIP_NPZ \
  STAGE_D_CLIP_MANIFEST; do
  test -n "${!required_var:-}"
done

test "${STAGE_D_COMPLETE:-false}" = true
test "${#BATCH_CLIP_NPZS[@]}" -eq 9
test "${#BATCH_CLIP_MANIFESTS[@]}" -eq 9

PHASE1C_PROVENANCE_COMMIT="$(git rev-parse HEAD)"
PHASE1C_PROVENANCE_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_${PHASE1C_PROVENANCE_COMMIT:0:12}"
PHASE1C_PROVENANCE_ROOT="/restricted/project/mimicecho/audits/lvef_multitask_phase1c_provenance_${PHASE1C_PROVENANCE_RUN_ID}"
PHASE1C_PROVENANCE_AGGREGATE="$PHASE1C_PROVENANCE_ROOT/aggregate"
PHASE1C_PROVENANCE_RESTRICTED="$PHASE1C_PROVENANCE_ROOT/restricted"

test ! -e "$PHASE1C_PROVENANCE_ROOT"
mkdir -p "$PHASE1C_PROVENANCE_AGGREGATE" "$PHASE1C_PROVENANCE_RESTRICTED"

COMPONENT_MANIFEST_ARGS=(
  --component-manifest "stage_d=$STAGE_D_CLIP_MANIFEST"
)
COMPONENT_EMBEDDING_ARGS=(
  --component-embedding-npz "stage_d=$STAGE_D_CLIP_NPZ"
)
for index in {0..8}; do
  printf -v component 'batch_%03d' "$index"
  COMPONENT_MANIFEST_ARGS+=(
    --component-manifest "$component=${BATCH_CLIP_MANIFESTS[$index]}"
  )
  COMPONENT_EMBEDDING_ARGS+=(
    --component-embedding-npz "$component=${BATCH_CLIP_NPZS[$index]}"
  )
done

set +e
"$PYTHON_BIN" scripts/audit_duplicate_clip_keys.py \
  "${COMPONENT_MANIFEST_ARGS[@]}" \
  "${COMPONENT_EMBEDDING_ARGS[@]}" \
  --merged-manifest "$MERGED_CLIP_MANIFEST" \
  --merged-embedding-npz "$MERGED_CLIP_NPZ" \
  --selected-studies "$SELECTED_STUDIES" \
  --expected-component-count 10 \
  --expected-duplicate-keys 32 \
  --expected-embedding-dim 512 \
  --vector-rtol 1e-6 \
  --vector-atol 1e-6 \
  --aggregate-output-dir "$PHASE1C_PROVENANCE_AGGREGATE" \
  --restricted-output-dir "$PHASE1C_PROVENANCE_RESTRICTED" \
  >"$PHASE1C_PROVENANCE_RESTRICTED/duplicate_clip_key_audit.stdout.txt" \
  2>"$PHASE1C_PROVENANCE_RESTRICTED/duplicate_clip_key_audit.stderr.txt"
DUPLICATE_AUDIT_STATUS=$?
set -e

# Exit 1 is required: the audit may classify duplicates but does not mutate or
# resolve the canonical store. Exit 0 would mean no duplicate groups; >=2 is an
# input/tooling blocker. The summary separately fails closed if the observed
# count is not the prespecified 32.
test "$DUPLICATE_AUDIT_STATUS" -eq 1

# LVEF_LABELS is used here only as the historical observed-LVEF-plus-imaging
# intersection authority. No prediction or metric file is opened.
"$PYTHON_BIN" scripts/audit_lvef_threshold_counts.py \
  --selected-studies "$SELECTED_STUDIES" \
  --structured-measurements "$STRUCTURED_MEASUREMENTS" \
  --imaging-eligible-studies "$LVEF_LABELS" \
  --split-map "$SPLIT_MAP" \
  --threshold 40 \
  --output-dir "$PHASE1C_PROVENANCE_AGGREGATE" \
  >"$PHASE1C_PROVENANCE_RESTRICTED/lvef_threshold_count.stdout.txt" \
  2>"$PHASE1C_PROVENANCE_RESTRICTED/lvef_threshold_count.stderr.txt"

"$PYTHON_BIN" - \
  "$PHASE1C_PROVENANCE_AGGREGATE" \
  >"$PHASE1C_PROVENANCE_AGGREGATE/phase1c_provenance_safety_gate.json" \
  2>"$PHASE1C_PROVENANCE_RESTRICTED/phase1c_provenance_safety_gate.stderr.txt" <<'PY'
import csv
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
issues = []

summary_path = root / "duplicate_clip_key_adjudication.summary.json"
counts_path = root / "duplicate_clip_key_reason_counts.csv"
duplicate_safety_path = root / "duplicate_clip_key_safety_gate.json"
threshold_path = root / "lvef_exact_threshold_counts.csv"
threshold_summary_path = root / "lvef_exact_threshold_counts.summary.json"
for path in (
    summary_path,
    counts_path,
    duplicate_safety_path,
    threshold_path,
    threshold_summary_path,
):
    if not path.is_file():
        issues.append("missing_expected_aggregate_file")

if not issues:
    duplicate_summary = json.loads(summary_path.read_text())
    duplicate_safety = json.loads(duplicate_safety_path.read_text())
    threshold_summary = json.loads(threshold_summary_path.read_text())
    duplicate_rows = list(csv.DictReader(counts_path.open(newline="")))
    threshold_rows = list(csv.DictReader(threshold_path.open(newline="")))

    if duplicate_summary.get("status") != "AUDIT_COMPLETE_BLOCKING_DUPLICATES":
        issues.append("duplicate_audit_not_complete_at_expected_count")
    if duplicate_summary.get("n_duplicate_groups") != 32:
        issues.append("unexpected_duplicate_group_count")
    if duplicate_summary.get("expected_duplicate_group_count_matches") is not True:
        issues.append("expected_duplicate_count_mismatch")
    if duplicate_summary.get("all_duplicate_groups_selected") is not True:
        issues.append("duplicate_group_outside_selected_or_ownership_failure")
    if duplicate_safety.get("aggregate_safety_gate_passed") is not True:
        issues.append("duplicate_aggregate_safety_failure")
    if threshold_summary.get("status") != "PASS" or len(threshold_rows) != 8:
        issues.append("threshold_count_audit_failure")

    allowed_duplicate_columns = {
        "classification",
        "component",
        "selected_scope",
        "proposed_resolution",
        "n_unique_duplicate_groups",
    }
    allowed_threshold_columns = {
        "cohort_scope",
        "split",
        "threshold",
        "comparison",
        "n_observed_lvef",
        "n_labels_exactly_equal_threshold",
    }
    if duplicate_rows and set(duplicate_rows[0]) != allowed_duplicate_columns:
        issues.append("unexpected_duplicate_count_schema")
    if threshold_rows and set(threshold_rows[0]) != allowed_threshold_columns:
        issues.append("unexpected_threshold_count_schema")

    aggregate_text = "\n".join(
        path.read_text(errors="replace")
        for path in root.iterdir()
        if path.is_file() and path.name != "phase1c_provenance_safety_gate.json"
    )
    forbidden = re.compile(
        r"(?:subject_id|study_id|dicom_filepath|npz_path|embedding_idx|"
        r"/restricted/|\\\\|[0-9a-f]{64})",
        re.IGNORECASE,
    )
    if forbidden.search(aggregate_text):
        issues.append("restricted_token_or_value_in_aggregate_output")

print(
    json.dumps(
        {
            "n_expected_aggregate_files_checked": 5,
            "n_issues": len(issues),
            "restricted_detail_files_printed": False,
            "safety_gate_passed": not issues,
        },
        sort_keys=True,
    )
)
if issues:
    raise SystemExit(2)
PY

cat "$PHASE1C_PROVENANCE_AGGREGATE/duplicate_clip_key_adjudication.summary.json"
cat "$PHASE1C_PROVENANCE_AGGREGATE/duplicate_clip_key_reason_counts.csv"
cat "$PHASE1C_PROVENANCE_AGGREGATE/duplicate_clip_key_safety_gate.json"
cat "$PHASE1C_PROVENANCE_AGGREGATE/lvef_exact_threshold_counts.summary.json"
cat "$PHASE1C_PROVENANCE_AGGREGATE/lvef_exact_threshold_counts.csv"
cat "$PHASE1C_PROVENANCE_AGGREGATE/phase1c_provenance_safety_gate.json"
```

## Safe outputs to paste back

Paste only the six files printed after the final gate passes. Do not paste:

- `duplicate_clip_groups_restricted.csv`;
- `duplicate_clip_rows_restricted.csv`;
- either audit's stdout/stderr from the restricted directory;
- the safety-gate stderr;
- any identifier, key, locator, file-content hash tied to a clip, label row, or embedding vector.

The aggregate duplicate classifications do not themselves resolve the embedding-authority gate. They determine whether Path A, B, or C in `embedding_authority_decision.md` is scientifically permissible. No re-embedding or deduplication is executed by this block.
