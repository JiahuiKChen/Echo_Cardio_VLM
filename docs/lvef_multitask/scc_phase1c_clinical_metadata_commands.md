# SCC Phase 1C clinical metadata review commands

This block creates a fresh SCC-only packet from the historical raw-to-canonical mapping. It reads metadata only. It refuses an input containing patient/study IDs, measurement values, predictions, embeddings, clip keys, or DICOM/NPZ locators; it refuses repository-local output; and it prints only aggregate counts and safety flags.

Run from the dedicated SCC worktree. Do not redirect either row-level CSV into Git or paste it into chat.

```bash
set -euo pipefail

cd "/restricted/project/mimicecho/code/Echo_Cardio_VLM_lvef_multitask"
test "$(git branch --show-current)" = "codex/lvef-multitask-revalidation"

FULLSCALE_ROOT="/restricted/project/mimicecho/code/Echo_Cardio_VLM/outputs/cloud_cohorts/fullscale_all"
RAW_CANONICAL_MAPPING="$FULLSCALE_ROOT/measurement_registry_v1/measurement_to_canonical_mapping.csv"
test -f "$RAW_CANONICAL_MAPPING"

RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PHASE1C_ROOT="/restricted/project/mimicecho/audits/lvef_multitask_phase1c_clinical_${RUN_STAMP}"
test ! -e "$PHASE1C_ROOT"
mkdir -p "$PHASE1C_ROOT/restricted"

python3 scripts/lvef_multitask_clinical_metadata.py \
  --mapping-csv "$RAW_CANONICAL_MAPPING" \
  --output-dir "$PHASE1C_ROOT/restricted"

python3 - "$PHASE1C_ROOT/restricted" <<'PY'
import csv
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
rows_path = root / "clinical_metadata_review_rows.csv"
summary_path = root / "clinical_metadata_canonical_summary.csv"
manifest_path = root / "clinical_metadata_review_packet_manifest.json"

expected = {rows_path.name, summary_path.name, manifest_path.name}
if {path.name for path in root.iterdir() if path.is_file()} != expected:
    raise SystemExit("unexpected_packet_inventory")

for path in (rows_path, summary_path):
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit("missing_csv_header")
        forbidden = {
            "subject_id", "subject", "patient_id", "person_id", "study_id", "study",
            "dicom_study_id", "identifier", "hadm_id", "stay_id", "mrn", "result",
            "result_numeric", "result_value", "measurement_value", "value",
            "label", "prediction", "y_true", "y_pred", "embedding", "embedding_idx",
            "clip_key", "dicom_path", "dicom_filepath", "npz_path", "path",
            "file_path", "absolute_path",
        }
        if forbidden & {name.strip().lower() for name in reader.fieldnames}:
            raise SystemExit("forbidden_column_in_packet")
        for _ in reader:
            pass

manifest = json.loads(manifest_path.read_text())
required_false = (
    "contains_patient_values",
    "contains_patient_or_study_identifiers",
    "contains_paths_or_locators",
    "canonical_candidates_outside_allowlist_are_authority",
    "model_fitting_performed",
    "test_performance_accessed",
)
if manifest.get("status") != "COMPLETE" or any(manifest.get(key) is not False for key in required_false):
    raise SystemExit("manifest_safety_assertion_failed")

for item in manifest.get("output_files", []):
    path = root / item["relative_path"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item["sha256"] or path.stat().st_size != item["bytes"]:
        raise SystemExit("packet_checksum_mismatch")

print(json.dumps({
    "status": "PASS",
    "n_allowlisted_targets_requested": manifest["n_allowlisted_targets_requested"],
    "n_allowlisted_targets_present": manifest["n_allowlisted_targets_present"],
    "n_packet_rows": manifest["n_packet_rows"],
    "n_candidate_canonical_mappings_outside_allowlist": manifest[
        "n_candidate_canonical_mappings_outside_allowlist"
    ],
    "metadata_rows_remain_restricted": True,
    "patient_level_content_emitted": False,
    "model_fitting_performed": False,
    "test_performance_accessed": False,
}, indent=2, sort_keys=True))
PY

printf '%s\n' "Restricted clinician inputs:"
printf '%s\n' "$PHASE1C_ROOT/restricted/clinical_metadata_review_rows.csv"
printf '%s\n' "$PHASE1C_ROOT/restricted/clinical_metadata_canonical_summary.csv"
printf '%s\n' "Git-safe file to paste back after the gate passes:"
printf '%s\n' "$PHASE1C_ROOT/restricted/clinical_metadata_review_packet_manifest.json"
```

The two CSVs intentionally remain restricted because they contain source-specific names and descriptions. The JSON manifest is aggregate-only and may be returned after the final gate passes. A candidate canonical mapping outside the allowlist is a search hit for review, never evidence that a field is present in the modeling panel or that its mapping is correct.
