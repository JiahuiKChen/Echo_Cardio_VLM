#!/usr/bin/env bash
# Restricted, outcome-blind Phase 1E-B/C metadata audit and clinician packet.
set -euo pipefail
umask 077

: "${LVEF_METADATA_ADJUDICATION_ENV_FILE:?LVEF_METADATA_ADJUDICATION_ENV_FILE is required}"
test -f "$LVEF_METADATA_ADJUDICATION_ENV_FILE"
test -O "$LVEF_METADATA_ADJUDICATION_ENV_FILE"
test "$(stat -c '%a' "$LVEF_METADATA_ADJUDICATION_ENV_FILE")" = "600"
# The owner-created SCC-only file contains shell-escaped scalar assignments.
source "$LVEF_METADATA_ADJUDICATION_ENV_FILE"

: "${WORKTREE:?}"
: "${EXPECTED_COMMIT:?}"
: "${PYTHON:?}"
: "${SAFE_EXPORT_POLICY:?}"
: "${EXPECTED_SAFE_EXPORT_POLICY_SHA256:?}"
: "${CLINICAL_REVIEW_ROWS:?}"
: "${EXPECTED_CLINICAL_REVIEW_ROWS_SHA256:?}"
: "${RAW_CANONICAL_MAPPING:?}"
: "${EXPECTED_RAW_CANONICAL_MAPPING_SHA256:?}"
: "${STRUCTURED_MEASUREMENTS:?}"
: "${EXPECTED_STRUCTURED_MEASUREMENTS_SHA256:?}"
: "${SELECTED_STUDIES:?}"
: "${EXPECTED_SELECTED_STUDIES_SHA256:?}"
: "${SPLIT_MAP:?}"
: "${EXPECTED_SPLIT_MAP_SHA256:?}"
: "${APPROVED_ORGANIZATION_CLASS:?}"
: "${RUN_ROOT:?}"

cd "$WORKTREE"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -z "$(git status --porcelain)"
test -x "$PYTHON"

for expected in \
  "$EXPECTED_SAFE_EXPORT_POLICY_SHA256" \
  "$EXPECTED_CLINICAL_REVIEW_ROWS_SHA256" \
  "$EXPECTED_RAW_CANONICAL_MAPPING_SHA256" \
  "$EXPECTED_STRUCTURED_MEASUREMENTS_SHA256" \
  "$EXPECTED_SELECTED_STUDIES_SHA256" \
  "$EXPECTED_SPLIT_MAP_SHA256"; do
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]]
done

test "$(sha256sum "$SAFE_EXPORT_POLICY" | awk '{print $1}')" = "$EXPECTED_SAFE_EXPORT_POLICY_SHA256"
test "$(sha256sum "$CLINICAL_REVIEW_ROWS" | awk '{print $1}')" = "$EXPECTED_CLINICAL_REVIEW_ROWS_SHA256"
test "$(sha256sum "$RAW_CANONICAL_MAPPING" | awk '{print $1}')" = "$EXPECTED_RAW_CANONICAL_MAPPING_SHA256"
test "$(sha256sum "$STRUCTURED_MEASUREMENTS" | awk '{print $1}')" = "$EXPECTED_STRUCTURED_MEASUREMENTS_SHA256"
test "$(sha256sum "$SELECTED_STUDIES" | awk '{print $1}')" = "$EXPECTED_SELECTED_STUDIES_SHA256"
test "$(sha256sum "$SPLIT_MAP" | awk '{print $1}')" = "$EXPECTED_SPLIT_MAP_SHA256"

if [[ -e "$RUN_ROOT" ]]; then
  test -d "$RUN_ROOT"
  test -z "$(find "$RUN_ROOT" -mindepth 1 -print -quit)"
fi
mkdir -p \
  "$RUN_ROOT/aggregate/technical_metadata" \
  "$RUN_ROOT/restricted/technical_metadata" \
  "$RUN_ROOT/restricted/clinician_signoff" \
  "$RUN_ROOT/restricted/logs"

DIRECT_PURPOSE='Phase 1E-B/C outcome-blind technical metadata audit and clinician packet preparation'
DIRECT_RECEIPT="$RUN_ROOT/restricted/approved_direct_restricted_agent_receipt.json"
COMMAND_SHA256="$(sha256sum scripts/scc_run_lvef_metadata_adjudication.sh | awk '{print $1}')"
"$PYTHON" scripts/lvef_multitask_analysis_modes.py \
  --policy "$SAFE_EXPORT_POLICY" direct-receipt \
  --receipt "$DIRECT_RECEIPT" \
  --purpose "$DIRECT_PURPOSE" \
  --source-commit "$EXPECTED_COMMIT" \
  --organization-class "$APPROVED_ORGANIZATION_CLASS" \
  --command-sha256 "$COMMAND_SHA256" \
  --hash-inputs \
  --input "$CLINICAL_REVIEW_ROWS" \
  --input "$RAW_CANONICAL_MAPPING" \
  --input "$STRUCTURED_MEASUREMENTS" \
  --input "$SELECTED_STUDIES" \
  --input "$SPLIT_MAP" \
  --output "$RUN_ROOT/restricted/technical_metadata" \
  --output "$RUN_ROOT/restricted/clinician_signoff" \
  --output "$RUN_ROOT/aggregate/technical_metadata" \
  >"$RUN_ROOT/restricted/logs/direct_receipt.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/direct_receipt.stderr.txt"

"$PYTHON" scripts/audit_lvef_multitask_technical_metadata.py \
  --clinical-review-rows-csv "$CLINICAL_REVIEW_ROWS" \
  --raw-canonical-mapping-csv "$RAW_CANONICAL_MAPPING" \
  --expected-raw-canonical-mapping-sha256 "$EXPECTED_RAW_CANONICAL_MAPPING_SHA256" \
  --structured-measurements-csv "$STRUCTURED_MEASUREMENTS" \
  --selected-studies-csv "$SELECTED_STUDIES" \
  --subject-split-map-csv "$SPLIT_MAP" \
  --safe-export-policy "$SAFE_EXPORT_POLICY" \
  --restricted-output-dir "$RUN_ROOT/restricted/technical_metadata" \
  --aggregate-output-dir "$RUN_ROOT/aggregate/technical_metadata" \
  >"$RUN_ROOT/restricted/logs/technical_metadata.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/technical_metadata.stderr.txt"

"$PYTHON" scripts/build_lvef_clinician_signoff_packet.py build \
  --clinical-review-rows-csv "$CLINICAL_REVIEW_ROWS" \
  --source-commit "$EXPECTED_COMMIT" \
  --safe-export-policy "$SAFE_EXPORT_POLICY" \
  --output-dir "$RUN_ROOT/restricted/clinician_signoff" \
  >"$RUN_ROOT/restricted/logs/clinician_packet.stdout.txt" \
  2>"$RUN_ROOT/restricted/logs/clinician_packet.stderr.txt"

"$PYTHON" -c '
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
gate = json.loads((root / "aggregate/technical_metadata/technical_metadata_safety_gate.json").read_text())
authority = json.loads((root / "aggregate/technical_metadata/lvef_separate_label_authority.json").read_text())
completeness = json.loads((root / "aggregate/technical_metadata/technical_metadata_completeness_summary.json").read_text())
packet = json.loads((root / "restricted/clinician_signoff/clinical_metadata_clinician_packet_manifest_restricted.json").read_text())
assert gate["status"] == "PASS" and gate["safety_gate_passed"] is True
assert gate["full_mapping_universe_completeness_proven"] is True
assert gate["lvef_method_lineage_complete"] is False
assert gate["confirmatory_performance_accessed"] is False
assert authority["status"] == "PASS"
assert authority["selected_preimaging_denominators_reconciled"] is True
assert authority["exact_40_counts_reconciled"] is True
assert completeness["mapping_universe_completeness_proven"] is True
assert completeness["review_packet_exactly_regenerated"] is True
assert packet["status"] == "READY_FOR_HUMAN_SIGNOFF"
assert packet["n_questions"] == 8 and packet["human_signoff_complete"] is False
' "$RUN_ROOT"

printf '%s\n' '{"clinician_questions":8,"confirmatory_performance_accessed":false,"status":"PASS_RESTRICTED_METADATA_AND_PACKET_READY"}'
