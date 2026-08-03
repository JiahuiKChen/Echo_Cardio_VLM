# SCC Phase 1B aggregate-only follow-up commands

These commands are limited to unresolved preservation-pack, clip-union, five-study attrition, and environment/checkpoint diagnostics. They do not fit models, open metric or prediction artifacts, read NPZ/embedding values, modify the historical freeze, or emit subject IDs, study IDs, clip keys, locator values, DICOM paths, or absolute source paths. The freeze diagnostic may emit only the hardcoded safe freeze-relative filenames already present in the Phase 1A aggregate inventory.

Run this block from the dedicated SCC worktree in the same Bash shell after sections 1–3 of `scc_revalidation_runbook.md`. Those sections define the historical roots, resolved artifact variables, Stage-D variables, and nine-element batch arrays. The block creates a fresh Phase 1B audit directory and refuses to reuse one.

Do not paste either restricted `stderr` file. If a command fails, inspect its restricted error locally because a traceback can contain source paths. Paste only the files printed after the final safety gate passes.

```bash
set -euo pipefail

for required_var in \
  PYTHON_BIN \
  FREEZE_ROOT \
  FULLSCALE_ROOT \
  SELECTED_STUDIES \
  STRUCTURED_MEASUREMENTS \
  SPLIT_MAP \
  STRICT_PANEL \
  MERGED_CLIP_MANIFEST \
  STUDY_EMBEDDING_MANIFEST \
  STAGE_D_DICOM_AUDIT \
  STAGE_D_CINE_CANDIDATES \
  STAGE_D_EXTRACTION \
  STAGE_D_CLIP_MANIFEST \
  ECHOPRIME_ENCODER; do
  test -n "${!required_var:-}"
done

test "${STAGE_D_COMPLETE:-false}" = true
test "${#BATCH_CLIP_MANIFESTS[@]}" -eq 9
test "${#BATCH_DICOM_AUDITS[@]}" -eq 9
test "${#BATCH_CINE_CANDIDATES[@]}" -eq 9
test "${#BATCH_EXTRACTION_MANIFESTS[@]}" -eq 9

PHASE1B_COMMIT="$(git rev-parse HEAD)"
PHASE1B_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_${PHASE1B_COMMIT:0:12}"
PHASE1B_FOLLOWUP_ROOT="/restricted/project/mimicecho/audits/lvef_multitask_phase1b_followup_${PHASE1B_RUN_ID}"
PHASE1B_AGGREGATE_DIR="$PHASE1B_FOLLOWUP_ROOT/aggregate"
PHASE1B_RESTRICTED_DIR="$PHASE1B_FOLLOWUP_ROOT/restricted"

test ! -e "$PHASE1B_FOLLOWUP_ROOT"
mkdir -p "$PHASE1B_AGGREGATE_DIR" "$PHASE1B_RESTRICTED_DIR"

# Freeze SHA manifest: global status counts, but filenames only from the
# hardcoded safe allowlist already exported by Phase 1A.
"$PYTHON_BIN" - "$FREEZE_ROOT" "$PHASE1B_AGGREGATE_DIR" \
  >"$PHASE1B_AGGREGATE_DIR/freeze_manifest_followup.summary.json" \
  2>"$PHASE1B_RESTRICTED_DIR/freeze_manifest_followup.stderr.txt" <<'PY'
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2])
manifest = root / "SHA256SUMS.txt"
allowlisted = {
    "eval/echoprime_embedding_baseline_metrics.json",
    "eval/fusion_comparison_table.csv",
    "eval/fusion_metrics.json",
    "eval/measurement_leakage_audit.json",
    "eval/tabular_baseline_metrics.json",
    "manifests/all_eligible_studies.csv",
    "manifests/lvef_still_manifest.csv",
    "manifests/lvef_still_manifest.summary.json",
    "manifests/structured_measurements.summary.json",
    "manifests/subject_split_map_v1.csv",
    "manifests/subject_split_map_v1.summary.json",
    "meta/fullscale_audit_report.md",
    "meta/fullscale_audit_summary.json",
}
status_order = [
    "ok",
    "malformed_manifest_line",
    "invalid_relative_path",
    "duplicate_manifest_entry",
    "missing_file",
    "invalid_file_type",
    "byte_mismatch",
    "unlisted_file",
]
counts = Counter()
details = []
valid_names = set()
seen = Counter()
line_count = 0


def record(status, name=None):
    counts[status] += 1
    if name in allowlisted:
        details.append({"status": status, "relative_filename": name})


if not manifest.is_file():
    counts["missing_checksum_manifest"] = 1
else:
    for raw in manifest.read_text(errors="replace").splitlines():
        line_count += 1
        match = re.fullmatch(r"([0-9A-Fa-f]{64}) ([ *])(.+)", raw)
        if raw.startswith("\\") or match is None:
            record("malformed_manifest_line")
            continue
        expected, _mode, name = match.groups()
        pure = PurePosixPath(name)
        if (
            "\\" in name
            or pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.as_posix() != name
        ):
            record("invalid_relative_path")
            continue
        candidate = (root / Path(*pure.parts)).resolve(strict=False)
        if candidate != root and root not in candidate.parents:
            record("invalid_relative_path")
            continue
        valid_names.add(name)
        seen[name] += 1
        if seen[name] > 1:
            record("duplicate_manifest_entry", name)
            continue
        source = root.joinpath(*pure.parts)
        if not source.exists():
            record("missing_file", name)
            continue
        if source.is_symlink() or not source.is_file():
            record("invalid_file_type", name)
            continue
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        record("ok" if actual.lower() == expected.lower() else "byte_mismatch", name)

for source in root.rglob("*"):
    if not (source.is_file() or source.is_symlink()):
        continue
    relative = source.relative_to(root).as_posix()
    if relative == "SHA256SUMS.txt":
        continue
    if relative not in valid_names:
        record("unlisted_file", relative)

with (out / "freeze_manifest_status_counts.csv").open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["status", "count"])
    writer.writeheader()
    for status in status_order + sorted(set(counts) - set(status_order)):
        writer.writerow({"status": status, "count": counts[status]})
with (out / "freeze_manifest_allowlisted_details.csv").open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["status", "relative_filename"])
    writer.writeheader()
    writer.writerows(
        sorted(details, key=lambda row: (row["status"], row["relative_filename"]))
    )

print(
    json.dumps(
        {
            "status": (
                "PASS"
                if sum(counts[status] for status in counts if status != "ok") == 0
                else "DISCREPANCY"
            ),
            "n_manifest_lines": line_count,
            "n_allowlisted_safe_filenames": len(allowlisted),
            "n_non_ok_events": sum(
                counts[status] for status in counts if status != "ok"
            ),
        },
        sort_keys=True,
    )
)
PY

phase1b_run_environment() {
# Environment and checkpoint provenance: only hardcoded safe meta-relative
# names, hashes, byte counts, and keyword-presence counts are exported. Raw
# metadata content and operational paths remain unexported.
"$PYTHON_BIN" - "$FREEZE_ROOT" "$ECHOPRIME_ENCODER" "$PHASE1B_AGGREGATE_DIR" \
  >"$PHASE1B_AGGREGATE_DIR/environment_checkpoint_followup.summary.json" \
  2>"$PHASE1B_RESTRICTED_DIR/environment_checkpoint_followup.stderr.txt" <<'PY'
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

freeze = Path(sys.argv[1])
checkpoint = Path(sys.argv[2])
out = Path(sys.argv[3])
meta_relatives = [
    "meta/fullscale_audit_report.md",
    "meta/fullscale_audit_summary.json",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if not checkpoint.is_file() or checkpoint.name != "echo_prime_encoder.pt":
    raise SystemExit("expected_encoder_checkpoint_missing_or_wrong_basename")
checkpoint_sha = digest(checkpoint)
checkpoint_name = "echo_prime_encoder.pt"
meta_rows = []
texts = []
for relative in meta_relatives:
    source = freeze / relative
    if not source.is_file():
        meta_rows.append(
            {
                "relative_filename": relative,
                "status": "MISSING",
                "bytes": 0,
                "sha256": "NA",
            }
        )
        continue
    payload = source.read_bytes()
    texts.append(payload.decode("utf-8", errors="replace"))
    meta_rows.append(
        {
            "relative_filename": relative,
            "status": "OK",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
joined = "\n".join(texts)
indicators = {
    "checkpoint_filename_exact_mention": len(
        re.findall(re.escape(checkpoint_name), joined, flags=re.I)
    ),
    "checkpoint_sha256_exact_mention_present": len(
        re.findall(re.escape(checkpoint_sha), joined, flags=re.I)
    ),
    "python_environment_marker": len(
        re.findall(r"\bpython(?:[_ -]?version)?\b", joined, flags=re.I)
    ),
    "package_inventory_marker": len(
        re.findall(
            r"pip freeze|requirements(?:\.txt)?|conda env|environment\.ya?ml|package versions?",
            joined,
            flags=re.I,
        )
    ),
    "pytorch_marker": len(
        re.findall(r"\b(?:torch|pytorch)(?:[_ -]?version)?\b", joined, flags=re.I)
    ),
    "cuda_marker": len(
        re.findall(r"\bcuda(?:[_ -]?version)?\b", joined, flags=re.I)
    ),
    "cudnn_marker": len(
        re.findall(r"\bcudnn(?:[_ -]?version)?\b", joined, flags=re.I)
    ),
    "source_commit_marker": len(
        re.findall(r"\b(?:git|source)[_ -]?commit\b", joined, flags=re.I)
    ),
}

with (out / "current_checkpoint_identity.csv").open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["filename", "bytes", "sha256"])
    writer.writeheader()
    writer.writerow(
        {
            "filename": checkpoint_name,
            "bytes": checkpoint.stat().st_size,
            "sha256": checkpoint_sha,
        }
    )
with (out / "freeze_meta_file_identity.csv").open("w", newline="") as handle:
    writer = csv.DictWriter(
        handle, fieldnames=["relative_filename", "status", "bytes", "sha256"]
    )
    writer.writeheader()
    writer.writerows(meta_rows)
with (out / "environment_checkpoint_evidence_indicators.csv").open(
    "w", newline=""
) as handle:
    writer = csv.DictWriter(
        handle, fieldnames=["indicator", "present", "occurrence_count"]
    )
    writer.writeheader()
    for indicator, count in sorted(indicators.items()):
        writer.writerow(
            {
                "indicator": indicator,
                "present": bool(count),
                "occurrence_count": count,
            }
        )

print(
    json.dumps(
        {
            "status": "COMPLETE",
            "n_expected_meta_files": len(meta_relatives),
            "n_present_meta_files": sum(
                row["status"] == "OK" for row in meta_rows
            ),
            "checkpoint_sha256_exact_mention_present": bool(
                indicators["checkpoint_sha256_exact_mention_present"]
            ),
            "checkpoint_hash_mention_is_candidate_evidence_only": True,
            "historical_embedding_use_proven": False,
            "historical_embedding_use_requires_contextual_run_manifest_linkage": True,
            "environment_markers_are_presence_only": True,
        },
        sort_keys=True,
    )
)
PY
}

phase1b_run_final_gate() {
# Final allowlist gate. It checks every CSV written by this block, controlled
# row vocabularies, and the only permitted relative filenames.
"$PYTHON_BIN" - "$PHASE1B_AGGREGATE_DIR" \
  >"$PHASE1B_AGGREGATE_DIR/aggregate_followup_safety_gate.json" \
  2>"$PHASE1B_RESTRICTED_DIR/aggregate_followup_safety_gate.stderr.txt" <<'PY'
import csv
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected_headers = {
    "freeze_manifest_status_counts.csv": ["status", "count"],
    "freeze_manifest_allowlisted_details.csv": ["status", "relative_filename"],
    "clip_component_union_discrepancy_counts.csv": [
        "discrepancy_reason",
        "component",
        "selected_scope",
        "last_successful_stage",
        "provenance_implication",
        "n_unique_clip_keys",
    ],
    "selected_without_embedding_attrition_counts.csv": [
        "reason_category",
        "split",
        "last_successful_stage",
        "n_studies",
        "n_with_numeric_exact_raw_lvef_preimaging",
        "n_with_any_legacy29_label",
    ],
    "current_checkpoint_identity.csv": ["filename", "bytes", "sha256"],
    "freeze_meta_file_identity.csv": [
        "relative_filename",
        "status",
        "bytes",
        "sha256",
    ],
    "environment_checkpoint_evidence_indicators.csv": [
        "indicator",
        "present",
        "occurrence_count",
    ],
}
forbidden_headers = {
    "subject_id",
    "study_id",
    "clip_key",
    "dicom_filepath",
    "npz_path",
    "absolute_path",
    "embedding",
    "y_true",
    "y_pred",
    "label",
    "prediction",
}
allowed_freeze_names = {
    "eval/echoprime_embedding_baseline_metrics.json",
    "eval/fusion_comparison_table.csv",
    "eval/fusion_metrics.json",
    "eval/measurement_leakage_audit.json",
    "eval/tabular_baseline_metrics.json",
    "manifests/all_eligible_studies.csv",
    "manifests/lvef_still_manifest.csv",
    "manifests/lvef_still_manifest.summary.json",
    "manifests/structured_measurements.summary.json",
    "manifests/subject_split_map_v1.csv",
    "manifests/subject_split_map_v1.summary.json",
    "meta/fullscale_audit_report.md",
    "meta/fullscale_audit_summary.json",
}
allowed_freeze_statuses = {
    "ok",
    "malformed_manifest_line",
    "invalid_relative_path",
    "duplicate_manifest_entry",
    "missing_file",
    "invalid_file_type",
    "byte_mismatch",
    "unlisted_file",
    "missing_checksum_manifest",
}
allowed_components = {
    "stage_d",
    "merged_only",
    "multiple_components",
    *(f"batch_{index:03d}" for index in range(9)),
}
allowed_scopes = {"selected", "outside_selected", "unknown_missing_study"}
allowed_clip_stages = {
    "study_aggregation",
    "merged_clip_embedding",
    "component_clip_embedding",
    "unknown",
}
allowed_implications = {
    "BLOCKING",
    "REVIEW_REQUIRED",
    "NONBLOCKING_EXPECTED_TRANSFORM",
}
allowed_payload_suffixes = {
    "subject_id",
    "study_id",
    "dicom_filepath",
    "npz_path",
    "view_id",
    "view_name",
    "embedding_l2_norm",
    "write_ok",
    "error",
}
fixed_clip_reasons = {
    "SOURCE_KEY_MISSING_FROM_MERGED",
    "MERGED_KEY_MISSING_FROM_COMPONENT_UNION",
    "SOURCE_MULTIPLICITY_EXCESS",
    "MERGED_MULTIPLICITY_EXCESS",
    "DUPLICATE_KEY_IN_COMPONENT_UNION",
    "DUPLICATE_KEY_IN_MERGED_MANIFEST",
    "COMPONENT_ROW_MISSING_KEY_FIELD",
    "MERGED_ROW_MISSING_KEY_FIELD",
    "SUBJECT_STUDY_OWNERSHIP_MISMATCH",
    "ORIGINAL_FULL_ROW_PAYLOAD_TUPLE_MISMATCH",
    "FULL_ROW_TUPLE_RESIDUAL_MISMATCH",
    "EMBEDDING_INDEX_REWRITE",
    "COMPONENT_ROW_ORDER_DIFFERENCE",
}
allowed_attrition_reasons = {
    "DOWNLOAD_FAILURE_OR_ABSENCE",
    "READABLE_DICOM_FAILURE_OR_ABSENCE",
    "NO_MULTIFRAME_CINE_CANDIDATE",
    "EXTRACTION_FAILURE_OR_ABSENCE",
    "CLIP_EMBEDDING_FAILURE_OR_ABSENCE",
    "STUDY_AGGREGATION_FAILURE_OR_ABSENCE",
    "INDETERMINATE_OR_NONMONOTONIC",
}
allowed_attrition_stages = {
    "selected",
    "downloaded",
    "readable_dicom",
    "cine_candidate",
    "extracted_clip",
    "clip_embedding",
    "study_aggregation",
}
allowed_indicators = {
    "checkpoint_filename_exact_mention",
    "checkpoint_sha256_exact_mention_present",
    "python_environment_marker",
    "package_inventory_marker",
    "pytorch_marker",
    "cuda_marker",
    "cudnn_marker",
    "source_commit_marker",
}
issues = []
tables = {}
expected_json = {
    "freeze_manifest_followup.summary.json": {
        "status": str,
        "n_manifest_lines": int,
        "n_allowlisted_safe_filenames": int,
        "n_non_ok_events": int,
    },
    "clip_component_union_followup.summary.json": {
        "status": str,
        "n_component_manifests": int,
        "n_grouped_output_rows": int,
        "n_unique_blocking_clip_keys": int,
        "n_unique_review_required_clip_keys": int,
        "n_original_full_row_payload_mismatch_keys": int,
        "original_payload_mismatch_matches_phase1a_9605": bool,
        "n_full_row_tuple_residual_mismatch_keys": int,
        "component_counts_are_memberships_and_may_overlap": bool,
        "n_unexpected_payload_columns_names_withheld": int,
        "locator_schema_column_count": int,
    },
    "selected_without_embedding_attrition.summary.json": {
        "status": str,
        "n_selected_without_study_embedding": int,
        "n_grouped_output_rows": int,
        "n_with_numeric_exact_raw_lvef_preimaging": int,
        "n_with_any_legacy29_label": int,
    },
    "environment_checkpoint_followup.summary.json": {
        "status": str,
        "n_expected_meta_files": int,
        "n_present_meta_files": int,
        "checkpoint_sha256_exact_mention_present": bool,
        "checkpoint_hash_mention_is_candidate_evidence_only": bool,
        "historical_embedding_use_proven": bool,
        "historical_embedding_use_requires_contextual_run_manifest_linkage": bool,
        "environment_markers_are_presence_only": bool,
    },
}

for name, expected in expected_headers.items():
    source = root / name
    if not source.is_file():
        issues.append(f"missing:{name}")
        continue
    with source.open(newline="", errors="replace") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        rows = list(reader)
    tables[name] = rows
    if header != expected:
        issues.append(f"unexpected_header:{name}")
    if forbidden_headers & {column.casefold() for column in header}:
        issues.append(f"forbidden_header:{name}")


def contains_unsafe_string(value):
    if isinstance(value, str):
        return (
            value.startswith("/")
            or "/restricted/" in value
            or "dicom" in value.casefold()
            and ("/" in value or "\\" in value)
        )
    if isinstance(value, dict):
        return any(contains_unsafe_string(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_unsafe_string(child) for child in value)
    return False


json_checked = 0
json_payloads = {}
for name, schema in expected_json.items():
    source = root / name
    if not source.is_file():
        issues.append(f"missing:{name}")
        continue
    try:
        payload = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError):
        issues.append(f"invalid_json:{name}")
        continue
    json_checked += 1
    json_payloads[name] = payload
    if not isinstance(payload, dict) or set(payload) != set(schema):
        issues.append(f"unexpected_json_schema:{name}")
        continue
    for key, expected_type in schema.items():
        if type(payload[key]) is not expected_type:
            issues.append(f"unexpected_json_type:{name}:{key}")
    if contains_unsafe_string(payload):
        issues.append(f"unsafe_json_string:{name}")

if tables.get("freeze_manifest_status_counts.csv") is None:
    issues.append("freeze_status_table_not_loaded")
if json_payloads.get("freeze_manifest_followup.summary.json", {}).get("status") not in {
    "PASS",
    "DISCREPANCY",
}:
    issues.append("unexpected_freeze_summary_status")
if json_payloads.get("clip_component_union_followup.summary.json", {}).get("status") not in {
    "PASS",
    "REVIEW_REQUIRED",
    "DISCREPANCY",
}:
    issues.append("unexpected_clip_summary_status")
if json_payloads.get("selected_without_embedding_attrition.summary.json", {}).get("status") != "COMPLETE":
    issues.append("unexpected_attrition_summary_status")
environment_summary = json_payloads.get(
    "environment_checkpoint_followup.summary.json", {}
)
if environment_summary.get("status") != "COMPLETE":
    issues.append("unexpected_environment_summary_status")
if environment_summary.get("historical_embedding_use_proven") is not False:
    issues.append("historical_embedding_use_must_remain_unproven")

for row in tables.get("freeze_manifest_status_counts.csv", []):
    if row.get("status") not in allowed_freeze_statuses:
        issues.append("unexpected_freeze_status")
    if not re.fullmatch(r"[0-9]+", row.get("count", "")):
        issues.append("invalid_freeze_status_count")
for name in (
    "freeze_manifest_allowlisted_details.csv",
    "freeze_meta_file_identity.csv",
):
    for row in tables.get(name, []):
        if row.get("relative_filename") not in allowed_freeze_names:
            issues.append(f"nonallowlisted_filename:{name}")
for row in tables.get("freeze_manifest_allowlisted_details.csv", []):
    if row.get("status") not in allowed_freeze_statuses:
        issues.append("unexpected_freeze_detail_status")

for row in tables.get("clip_component_union_discrepancy_counts.csv", []):
    reason = row.get("discrepancy_reason", "")
    variable_reason_ok = False
    for prefix in (
        "TEXT_NORMALIZATION_REVIEW__",
        "NUMERIC_SERIALIZATION_REVIEW__",
        "NON_INDEX_PAYLOAD_MISMATCH__",
    ):
        if reason.startswith(prefix) and reason.removeprefix(prefix) in allowed_payload_suffixes:
            variable_reason_ok = True
    if reason not in fixed_clip_reasons and not variable_reason_ok:
        issues.append("unexpected_clip_reason")
    if row.get("component") not in allowed_components:
        issues.append("unexpected_clip_component")
    if row.get("selected_scope") not in allowed_scopes:
        issues.append("unexpected_clip_scope")
    if row.get("last_successful_stage") not in allowed_clip_stages:
        issues.append("unexpected_clip_stage")
    if row.get("provenance_implication") not in allowed_implications:
        issues.append("unexpected_clip_implication")
    if not re.fullmatch(r"[0-9]+", row.get("n_unique_clip_keys", "")):
        issues.append("invalid_clip_count")

for row in tables.get("selected_without_embedding_attrition_counts.csv", []):
    if row.get("reason_category") not in allowed_attrition_reasons:
        issues.append("unexpected_attrition_reason")
    if row.get("split") not in {"train", "val", "test", "unassigned"}:
        issues.append("unexpected_attrition_split")
    if row.get("last_successful_stage") not in allowed_attrition_stages:
        issues.append("unexpected_attrition_stage")
    for column in (
        "n_studies",
        "n_with_numeric_exact_raw_lvef_preimaging",
        "n_with_any_legacy29_label",
    ):
        if not re.fullmatch(r"[0-9]+", row.get(column, "")):
            issues.append(f"invalid_attrition_count:{column}")

checkpoint_rows = tables.get("current_checkpoint_identity.csv", [])
if len(checkpoint_rows) != 1 or checkpoint_rows[0].get("filename") != "echo_prime_encoder.pt":
    issues.append("unexpected_checkpoint_filename")
for row in checkpoint_rows:
    if not re.fullmatch(r"[0-9]+", row.get("bytes", "")):
        issues.append("invalid_checkpoint_bytes")
    if not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")):
        issues.append("invalid_checkpoint_sha256")
for row in tables.get("freeze_meta_file_identity.csv", []):
    if row.get("status") not in {"OK", "MISSING"}:
        issues.append("unexpected_meta_status")
    if not re.fullmatch(r"[0-9]+", row.get("bytes", "")):
        issues.append("invalid_meta_bytes")
    if row.get("status") == "OK" and not re.fullmatch(
        r"[0-9a-f]{64}", row.get("sha256", "")
    ):
        issues.append("invalid_meta_sha256")
    if row.get("status") == "MISSING" and row.get("sha256") != "NA":
        issues.append("invalid_missing_meta_sha256")
for row in tables.get("environment_checkpoint_evidence_indicators.csv", []):
    if row.get("indicator") not in allowed_indicators:
        issues.append("unexpected_environment_indicator")
    if row.get("present") not in {"True", "False"}:
        issues.append("unexpected_indicator_boolean")
    if not re.fullmatch(r"[0-9]+", row.get("occurrence_count", "")):
        issues.append("invalid_indicator_count")

print(
    json.dumps(
        {
            "n_expected_csv_files": len(expected_headers),
            "n_csv_files_checked": len(tables),
            "n_expected_json_summaries": len(expected_json),
            "n_json_summaries_checked": json_checked,
            "n_issues": len(issues),
            "safety_gate_passed": not issues,
        },
        sort_keys=True,
    )
)
if issues:
    raise SystemExit(2)
PY

# Print only after the final allowlist gate passes.
cat "$PHASE1B_AGGREGATE_DIR/freeze_manifest_followup.summary.json"
cat "$PHASE1B_AGGREGATE_DIR/freeze_manifest_status_counts.csv"
cat "$PHASE1B_AGGREGATE_DIR/freeze_manifest_allowlisted_details.csv"
cat "$PHASE1B_AGGREGATE_DIR/clip_component_union_followup.summary.json"
cat "$PHASE1B_AGGREGATE_DIR/clip_component_union_discrepancy_counts.csv"
cat "$PHASE1B_AGGREGATE_DIR/selected_without_embedding_attrition.summary.json"
cat "$PHASE1B_AGGREGATE_DIR/selected_without_embedding_attrition_counts.csv"
cat "$PHASE1B_AGGREGATE_DIR/environment_checkpoint_followup.summary.json"
cat "$PHASE1B_AGGREGATE_DIR/current_checkpoint_identity.csv"
cat "$PHASE1B_AGGREGATE_DIR/freeze_meta_file_identity.csv"
cat "$PHASE1B_AGGREGATE_DIR/environment_checkpoint_evidence_indicators.csv"
cat "$PHASE1B_AGGREGATE_DIR/aggregate_followup_safety_gate.json"
}

phase1b_run_attrition() {
# Five selected studies without study embeddings. The exact raw numeric LVEF
# preimage is reconstructed before imaging linkage; no target values are read
# out. Legacy29 presence is any nonmissing task__ field in the wide authority.
FIVE_DICOM_ARGS=(--dicom-audit "$STAGE_D_DICOM_AUDIT")
FIVE_CINE_ARGS=(--cine-candidates "$STAGE_D_CINE_CANDIDATES")
FIVE_EXTRACTION_ARGS=(--extraction-manifest "$STAGE_D_EXTRACTION")
for item in "${BATCH_DICOM_AUDITS[@]}"; do
  FIVE_DICOM_ARGS+=(--dicom-audit "$item")
done
for item in "${BATCH_CINE_CANDIDATES[@]}"; do
  FIVE_CINE_ARGS+=(--cine-candidates "$item")
done
for item in "${BATCH_EXTRACTION_MANIFESTS[@]}"; do
  FIVE_EXTRACTION_ARGS+=(--extraction-manifest "$item")
done

"$PYTHON_BIN" - \
  --output-csv "$PHASE1B_AGGREGATE_DIR/selected_without_embedding_attrition_counts.csv" \
  --selected-studies "$SELECTED_STUDIES" \
  --structured-measurements "$STRUCTURED_MEASUREMENTS" \
  --split-map "$SPLIT_MAP" \
  --legacy29-panel "$STRICT_PANEL" \
  --merged-clip-manifest "$MERGED_CLIP_MANIFEST" \
  --study-embedding-manifest "$STUDY_EMBEDDING_MANIFEST" \
  "${FIVE_DICOM_ARGS[@]}" \
  "${FIVE_CINE_ARGS[@]}" \
  "${FIVE_EXTRACTION_ARGS[@]}" \
  >"$PHASE1B_AGGREGATE_DIR/selected_without_embedding_attrition.summary.json" \
  2>"$PHASE1B_RESTRICTED_DIR/selected_without_embedding_attrition.stderr.txt" <<'PY'
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path.cwd() / "scripts"))
import audit_lvef_multitask_artifacts as audit

parser = argparse.ArgumentParser()
parser.add_argument("--output-csv", type=Path, required=True)
parser.add_argument("--selected-studies", type=Path, required=True)
parser.add_argument("--structured-measurements", type=Path, required=True)
parser.add_argument("--split-map", type=Path, required=True)
parser.add_argument("--legacy29-panel", type=Path, required=True)
parser.add_argument("--merged-clip-manifest", type=Path, required=True)
parser.add_argument("--study-embedding-manifest", type=Path, required=True)
parser.add_argument("--dicom-audit", type=Path, action="append", required=True)
parser.add_argument("--cine-candidates", type=Path, action="append", required=True)
parser.add_argument("--extraction-manifest", type=Path, action="append", required=True)
args = parser.parse_args()


def read(path):
    return pd.read_csv(path, low_memory=False)


def canonical_study_set(frame):
    column = audit.resolve_column(frame, audit.STUDY_COLUMNS, required=True)
    return set(audit.canonical_identifier_series(frame[column]).dropna())


def successful_stage_set(stage_name, paths):
    frame = pd.concat([read(path) for path in paths], ignore_index=True, sort=False)
    successful, _flag = audit.successful_stage_rows(stage_name, frame)
    return canonical_study_set(successful)


selected_frame = read(args.selected_studies)
selected_subject_col = audit.resolve_column(
    selected_frame, audit.SUBJECT_COLUMNS, required=True
)
selected_study_col = audit.resolve_column(
    selected_frame, audit.STUDY_COLUMNS, required=True
)
selected = pd.DataFrame(
    {
        "_subject": audit.canonical_identifier_series(
            selected_frame[selected_subject_col]
        ),
        "_study": audit.canonical_identifier_series(selected_frame[selected_study_col]),
    }
).dropna()
if selected["_study"].duplicated().any():
    raise SystemExit("selected_study_mapping_not_unique")
study_to_subject = dict(zip(selected["_study"], selected["_subject"], strict=True))
selected_studies = set(study_to_subject)

split_frame = read(args.split_map)
split_subject_col = audit.resolve_column(
    split_frame, audit.SUBJECT_COLUMNS, required=True
)
split_col = audit.resolve_column(split_frame, audit.SPLIT_COLUMNS, required=True)
split_work = pd.DataFrame(
    {
        "_subject": audit.canonical_identifier_series(split_frame[split_subject_col]),
        "_split": split_frame[split_col].astype("string").str.strip().str.lower(),
    }
).dropna()
if split_work.groupby("_subject")["_split"].nunique().gt(1).any():
    raise SystemExit("conflicting_subject_split_assignment")
subject_to_split = dict(
    split_work.drop_duplicates("_subject").itertuples(index=False, name=None)
)

downloaded = successful_stage_set("downloaded_studies", args.dicom_audit)
readable = successful_stage_set("readable_dicoms", args.dicom_audit)
cine = successful_stage_set("cine_candidates", args.cine_candidates)
extracted = successful_stage_set("extracted_clips", args.extraction_manifest)
clip_success, _flag = audit.successful_stage_rows(
    "clip_embeddings", read(args.merged_clip_manifest)
)
clip_embedded = canonical_study_set(clip_success)
study_embedded = canonical_study_set(read(args.study_embedding_manifest))

missing = selected_studies - study_embedded
if len(missing) != 5:
    raise SystemExit("expected_exactly_five_selected_studies_without_embeddings")

# Historical builder semantics: exact case-sensitive measurement == "lvef",
# numeric result, median by subject/measurement, before imaging linkage.
lvef_preimage = audit.historical_selected_lvef_preimage(
    selected_frame, read(args.structured_measurements)
)
lvef_preimage_studies = set(lvef_preimage["study_id"])

panel = read(args.legacy29_panel)
task_columns = [column for column in panel.columns if str(column).startswith("task__")]
if len(task_columns) != 29:
    raise SystemExit("legacy29_panel_does_not_have_exactly_29_task_columns")
panel_study_col = audit.resolve_column(panel, audit.STUDY_COLUMNS, required=True)
panel_studies = audit.canonical_identifier_series(panel[panel_study_col])
any_legacy29 = panel[task_columns].notna().any(axis=1)
legacy29_present_studies = set(panel_studies[any_legacy29].dropna())


def classify(study):
    if study not in downloaded:
        reason = "DOWNLOAD_FAILURE_OR_ABSENCE"
    elif study not in readable:
        reason = "READABLE_DICOM_FAILURE_OR_ABSENCE"
    elif study not in cine:
        reason = "NO_MULTIFRAME_CINE_CANDIDATE"
    elif study not in extracted:
        reason = "EXTRACTION_FAILURE_OR_ABSENCE"
    elif study not in clip_embedded:
        reason = "CLIP_EMBEDDING_FAILURE_OR_ABSENCE"
    elif study not in study_embedded:
        reason = "STUDY_AGGREGATION_FAILURE_OR_ABSENCE"
    else:
        reason = "INDETERMINATE_OR_NONMONOTONIC"

    last_stage = "selected"
    for stage_name, stage_set in (
        ("downloaded", downloaded),
        ("readable_dicom", readable),
        ("cine_candidate", cine),
        ("extracted_clip", extracted),
        ("clip_embedding", clip_embedded),
        ("study_aggregation", study_embedded),
    ):
        if study in stage_set:
            last_stage = stage_name
    split = subject_to_split.get(study_to_subject[study], "unassigned")
    return reason, split, last_stage


groups = defaultdict(
    lambda: {
        "n_studies": 0,
        "n_with_numeric_exact_raw_lvef_preimaging": 0,
        "n_with_any_legacy29_label": 0,
    }
)
for study in missing:
    key = classify(study)
    groups[key]["n_studies"] += 1
    groups[key]["n_with_numeric_exact_raw_lvef_preimaging"] += int(
        study in lvef_preimage_studies
    )
    groups[key]["n_with_any_legacy29_label"] += int(
        study in legacy29_present_studies
    )

columns = [
    "reason_category",
    "split",
    "last_successful_stage",
    "n_studies",
    "n_with_numeric_exact_raw_lvef_preimaging",
    "n_with_any_legacy29_label",
]
rows = []
for (reason, split, last_stage), counts in groups.items():
    rows.append(
        {
            "reason_category": reason,
            "split": split,
            "last_successful_stage": last_stage,
            **counts,
        }
    )
with args.output_csv.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    writer.writerows(
        sorted(
            rows,
            key=lambda row: (
                row["reason_category"],
                row["split"],
                row["last_successful_stage"],
            ),
        )
    )

print(
    json.dumps(
        {
            "status": "COMPLETE",
            "n_selected_without_study_embedding": len(missing),
            "n_grouped_output_rows": len(rows),
            "n_with_numeric_exact_raw_lvef_preimaging": sum(
                study in lvef_preimage_studies for study in missing
            ),
            "n_with_any_legacy29_label": sum(
                study in legacy29_present_studies for study in missing
            ),
        },
        sort_keys=True,
    )
)
PY
}

# Clip-union provenance: identifier and locator values remain in memory. The
# only durable output is grouped counts by reason, source component, selected
# scope, last successful stage, and provenance implication.
"$PYTHON_BIN" - \
  "$PHASE1B_AGGREGATE_DIR" \
  "$SELECTED_STUDIES" \
  "$MERGED_CLIP_MANIFEST" \
  "$STUDY_EMBEDDING_MANIFEST" \
  "$STAGE_D_CLIP_MANIFEST" \
  "${BATCH_CLIP_MANIFESTS[@]}" \
  >"$PHASE1B_AGGREGATE_DIR/clip_component_union_followup.summary.json" \
  2>"$PHASE1B_RESTRICTED_DIR/clip_component_union_followup.stderr.txt" <<'PY'
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path.cwd() / "scripts"))
import audit_lvef_multitask_artifacts as audit

out = Path(sys.argv[1])
selected_path = Path(sys.argv[2])
merged_path = Path(sys.argv[3])
study_store_path = Path(sys.argv[4])
component_paths = [Path(value) for value in sys.argv[5:]]
if len(component_paths) != 10:
    raise SystemExit("expected_exactly_10_component_manifests")
component_labels = ["stage_d"] + [f"batch_{index:03d}" for index in range(9)]


def read(path):
    return pd.read_csv(path, low_memory=False)


merged_success = audit._successful_clip_manifest(read(merged_path))
component_success = [
    audit._successful_clip_manifest(read(path)) for path in component_paths
]
all_frames = [merged_success, *component_success]
locators = tuple(
    column
    for column in audit.CLIP_LOCATOR_COLUMNS
    if all(column in frame.columns for frame in all_frames)
)
if not locators:
    raise SystemExit("no_shared_clip_locator_schema")

merged = audit._prepare_clip_manifest_keys(merged_success, locators)
prepared_components = []
for label, frame in zip(component_labels, component_success, strict=True):
    prepared = audit._prepare_clip_manifest_keys(frame, locators)
    prepared["_component"] = label
    prepared_components.append(prepared)
source = pd.concat(prepared_components, ignore_index=True, sort=False)

source_counter = Counter(source["_audit_clip_key"])
merged_counter = Counter(merged["_audit_clip_key"])
source_keys = set(source_counter)
merged_keys = set(merged_counter)

component_by_key = defaultdict(set)
for key, component in zip(
    source["_audit_clip_key"], source["_component"], strict=True
):
    component_by_key[key].add(str(component))


def canonical_study_set(frame):
    column = audit.resolve_column(frame, audit.STUDY_COLUMNS, required=True)
    return set(audit.canonical_identifier_series(frame[column]).dropna())


selected_studies = canonical_study_set(read(selected_path))
study_store_studies = canonical_study_set(read(study_store_path))
source_studies = set(source["_audit_study"].dropna())
merged_studies = set(merged["_audit_study"].dropna())


def components_for(key):
    components = component_by_key.get(key, set())
    if not components:
        return ["merged_only"]
    # Emit every safe source-component membership. Cross-component duplicate
    # keys therefore contribute to each implicated component rather than being
    # collapsed into an uninformative multiple-components bucket.
    return sorted(components)


def scope_for(key):
    study = key[1] if len(key) > 1 else "<MISSING>"
    if study == "<MISSING>":
        return "unknown_missing_study"
    return "selected" if study in selected_studies else "outside_selected"


def last_stage_for(key):
    study = key[1] if len(key) > 1 else "<MISSING>"
    if study == "<MISSING>":
        return "unknown"
    if study in study_store_studies:
        return "study_aggregation"
    if study in merged_studies:
        return "merged_clip_embedding"
    if study in source_studies:
        return "component_clip_embedding"
    return "unknown"


groups = defaultdict(set)
blocking_keys = set()
review_keys = set()


def add(reason, key, implication="BLOCKING"):
    for component in components_for(key):
        group = (
            reason,
            component,
            scope_for(key),
            last_stage_for(key),
            implication,
        )
        groups[group].add(key)
    if implication == "BLOCKING":
        blocking_keys.add(key)
    elif implication == "REVIEW_REQUIRED":
        review_keys.add(key)


for key in source_keys - merged_keys:
    add("SOURCE_KEY_MISSING_FROM_MERGED", key)
for key in merged_keys - source_keys:
    add("MERGED_KEY_MISSING_FROM_COMPONENT_UNION", key)
for key in source_keys | merged_keys:
    source_n = source_counter[key]
    merged_n = merged_counter[key]
    if source_n != merged_n:
        add(
            (
                "SOURCE_MULTIPLICITY_EXCESS"
                if source_n > merged_n
                else "MERGED_MULTIPLICITY_EXCESS"
            ),
            key,
        )
    if source_n > 1:
        add("DUPLICATE_KEY_IN_COMPONENT_UNION", key)
    if merged_n > 1:
        add("DUPLICATE_KEY_IN_MERGED_MANIFEST", key)
for key in set(source.loc[source["_audit_key_missing"], "_audit_clip_key"]):
    add("COMPONENT_ROW_MISSING_KEY_FIELD", key)
for key in set(merged.loc[merged["_audit_key_missing"], "_audit_clip_key"]):
    add("MERGED_ROW_MISSING_KEY_FIELD", key)

# Compare ownership by locator in memory; do not emit locators or owners.
source_owner = defaultdict(set)
merged_owner = defaultdict(set)
source_keys_by_locator = defaultdict(set)
merged_keys_by_locator = defaultdict(set)
for key in source_keys:
    locator = key[2:]
    source_owner[locator].add(key[:2])
    source_keys_by_locator[locator].add(key)
for key in merged_keys:
    locator = key[2:]
    merged_owner[locator].add(key[:2])
    merged_keys_by_locator[locator].add(key)
for locator in set(source_owner) & set(merged_owner):
    if source_owner[locator] != merged_owner[locator]:
        for key in source_keys_by_locator[locator] | merged_keys_by_locator[locator]:
            add("SUBJECT_STUDY_OWNERSHIP_MISMATCH", key)


def values_by_key(frame, column):
    values = defaultdict(list)
    for key, value in zip(frame["_audit_clip_key"], frame[column], strict=True):
        values[key].append(value)
    return values


def canonical_counter(values):
    return Counter(audit._canonical_manifest_scalar(value) for value in values)


def normalized_text_counter(values):
    normalized = []
    for value in values:
        if pd.isna(value):
            normalized.append("<MISSING>")
        else:
            normalized.append(str(value).strip().casefold())
    return Counter(normalized)


def numeric_multisets_close(left, right):
    if len(left) != len(right):
        return False
    try:
        left_values = sorted(float(value) for value in left if not pd.isna(value))
        right_values = sorted(float(value) for value in right if not pd.isna(value))
    except (TypeError, ValueError):
        return False
    if len(left_values) != len(left) or len(right_values) != len(right):
        return False
    return all(
        math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
        for a, b in zip(left_values, right_values, strict=True)
    )


safe_payload_columns = [
    "subject_id",
    "study_id",
    "dicom_filepath",
    "npz_path",
    "view_id",
    "view_name",
    "embedding_l2_norm",
    "write_ok",
    "error",
]
union_columns = set(source.columns) | set(merged.columns)
unexpected_payload_columns = {
    column
    for column in union_columns
    if column
    not in set(safe_payload_columns) | {"embedding_idx", "_component"}
    and not column.startswith("_audit_")
}
shared_keys = source_keys & merged_keys

# Reproduce the original audit's full non-index row-payload tuple multiset.
# Per-column marginal multisets alone are insufficient: duplicate-key rows can
# swap values across columns while preserving every marginal distribution.
payload_columns = sorted(
    column
    for column in union_columns
    if column not in {"embedding_idx", "_component"}
    and not column.startswith("_audit_")
)
source_payload_by_key = defaultdict(Counter)
merged_payload_by_key = defaultdict(Counter)
for key, values in zip(
    source["_audit_clip_key"],
    source.reindex(columns=payload_columns).itertuples(index=False, name=None),
    strict=True,
):
    source_payload_by_key[key][
        tuple(audit._canonical_manifest_scalar(value) for value in values)
    ] += 1
for key, values in zip(
    merged["_audit_clip_key"],
    merged.reindex(columns=payload_columns).itertuples(index=False, name=None),
    strict=True,
):
    merged_payload_by_key[key][
        tuple(audit._canonical_manifest_scalar(value) for value in values)
    ] += 1
original_payload_mismatch_keys = {
    key
    for key in shared_keys
    if source_payload_by_key[key] != merged_payload_by_key[key]
}
for key in original_payload_mismatch_keys:
    add("ORIGINAL_FULL_ROW_PAYLOAD_TUPLE_MISMATCH", key, "REVIEW_REQUIRED")

per_column_mismatch_keys = set()
for column in safe_payload_columns:
    if column not in source.columns or column not in merged.columns:
        continue
    source_values = values_by_key(source, column)
    merged_values = values_by_key(merged, column)
    for key in shared_keys:
        left = source_values[key]
        right = merged_values[key]
        if canonical_counter(left) == canonical_counter(right):
            continue
        per_column_mismatch_keys.add(key)
        if normalized_text_counter(left) == normalized_text_counter(right):
            add(f"TEXT_NORMALIZATION_REVIEW__{column}", key, "REVIEW_REQUIRED")
        elif numeric_multisets_close(left, right):
            add(
                f"NUMERIC_SERIALIZATION_REVIEW__{column}",
                key,
                "REVIEW_REQUIRED",
            )
        else:
            add(f"NON_INDEX_PAYLOAD_MISMATCH__{column}", key)

tuple_residual_mismatch_keys = (
    original_payload_mismatch_keys - per_column_mismatch_keys
)
for key in tuple_residual_mismatch_keys:
    add("FULL_ROW_TUPLE_RESIDUAL_MISMATCH", key)

# The current union contract excludes embedding_idx and compares row
# multisets, so index rewrites and row ordering are measured but nonblocking.
if "embedding_idx" in source.columns and "embedding_idx" in merged.columns:
    source_index = values_by_key(source, "embedding_idx")
    merged_index = values_by_key(merged, "embedding_idx")
    for key in shared_keys:
        if canonical_counter(source_index[key]) != canonical_counter(
            merged_index[key]
        ):
            add(
                "EMBEDDING_INDEX_REWRITE",
                key,
                "NONBLOCKING_EXPECTED_TRANSFORM",
            )
if len(source) == len(merged):
    for source_key, merged_key in zip(
        source["_audit_clip_key"], merged["_audit_clip_key"], strict=True
    ):
        if source_key != merged_key:
            add(
                "COMPONENT_ROW_ORDER_DIFFERENCE",
                source_key,
                "NONBLOCKING_EXPECTED_TRANSFORM",
            )

rows = []
for group, keys in groups.items():
    reason, component, scope, last_stage, implication = group
    rows.append(
        {
            "discrepancy_reason": reason,
            "component": component,
            "selected_scope": scope,
            "last_successful_stage": last_stage,
            "provenance_implication": implication,
            "n_unique_clip_keys": len(keys),
        }
    )
columns = [
    "discrepancy_reason",
    "component",
    "selected_scope",
    "last_successful_stage",
    "provenance_implication",
    "n_unique_clip_keys",
]
with (out / "clip_component_union_discrepancy_counts.csv").open(
    "w", newline=""
) as handle:
    writer = csv.DictWriter(handle, fieldnames=columns)
    writer.writeheader()
    writer.writerows(
        sorted(rows, key=lambda row: tuple(str(row[column]) for column in columns[:-1]))
    )

print(
    json.dumps(
        {
            "status": (
                "DISCREPANCY"
                if blocking_keys or len(original_payload_mismatch_keys) != 9605
                else ("REVIEW_REQUIRED" if review_keys else "PASS")
            ),
            "n_component_manifests": len(component_paths),
            "n_grouped_output_rows": len(rows),
            "n_unique_blocking_clip_keys": len(blocking_keys),
            "n_unique_review_required_clip_keys": len(review_keys),
            "n_original_full_row_payload_mismatch_keys": len(
                original_payload_mismatch_keys
            ),
            "original_payload_mismatch_matches_phase1a_9605": (
                len(original_payload_mismatch_keys) == 9605
            ),
            "n_full_row_tuple_residual_mismatch_keys": len(
                tuple_residual_mismatch_keys
            ),
            "component_counts_are_memberships_and_may_overlap": True,
            "n_unexpected_payload_columns_names_withheld": len(
                unexpected_payload_columns
            ),
            "locator_schema_column_count": len(locators),
        },
        sort_keys=True,
    )
)
PY

phase1b_run_attrition
phase1b_run_environment
phase1b_run_final_gate
```

## Interpretation boundaries

- The verified Phase 1A aggregate reports `clip_keys_complete = True`; missing-key checks above are defensive and must not be described as an observed defect. The unresolved observed clip-union findings are 32 duplicate-key excess rows on each side and 9,605 non-index full-row payload-mismatch keys.
- The follow-up first reproduces the original full row-payload tuple multiset and explicitly checks whether it recovers all 9,605 mismatch keys. Per-column marginal checks are secondary: duplicate-key rows can swap values across columns while preserving every marginal multiset. Any `FULL_ROW_TUPLE_RESIDUAL_MISMATCH` therefore remains blocking.
- A payload mismatch may reflect raw formatting of a key-associated field because key preparation strips/normalizes locator values while payload comparison retains original values. `TEXT_NORMALIZATION_REVIEW__*` and `NUMERIC_SERIALIZATION_REVIEW__*` are not automatically excused. They retain `REVIEW_REQUIRED` status until an explicit normalization/tolerance policy is declared and rerun.
- Component counts are membership counts and can overlap: a cross-component duplicate is emitted once for every implicated safe component label rather than collapsed to `multiple_components`.
- `EMBEDDING_INDEX_REWRITE` and `COMPONENT_ROW_ORDER_DIFFERENCE` are explicitly nonblocking because the existing union contract excludes `embedding_idx` and compares row multisets. Key-set, multiplicity, ownership, full-tuple residual, or substantive non-index payload mismatches remain blocking.
- The attrition output uses the exact historical pre-imaging LVEF-label derivation (`measurement == "lvef"`, numeric result, median by subject/measurement) rather than the imaging-linked LVEF manifest, which necessarily excludes studies without embeddings.
- A current checkpoint hash is not historical-use proof. Even an exact hash mention in frozen metadata is candidate evidence only; `historical_embedding_use_proven` remains false until a contextual run manifest links that hash to the historical embedding job. Environment keyword presence does not establish package versions.
- These outputs may narrow the blockers but do not authorize model fitting, prediction regeneration, confirmatory performance access, freeze repair, or clinical-panel lock.
