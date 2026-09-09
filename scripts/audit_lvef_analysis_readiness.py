#!/usr/bin/env python3
"""Replay restricted metadata preparation and human signoff without fitting.

This is evidence inspection, not the analysis lock. In particular, a successful
technical packet remains pending scientific review and a generated questionnaire
does not constitute a clinician decision. No reconstruction authority is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

import pandas as pd

from audit_lvef_multitask_technical_metadata import TECHNICAL_ISSUE_IDS
from build_multitask_target_panel import normalize_unit
from lvef_multitask_clinical_metadata import ALLOWED_TARGETS, METHOD_PATTERN, INDEXED_PATTERN
from build_lvef_clinician_signoff_packet import (
    CLINICAL_ISSUE_IDS, CLINICAL_ISSUE_SPECS, build_packet, validate_response,
    OWNER_RELAY_MODE, OWNER_RELAY_SOURCE_SHA256, OWNER_RELAY_INPUT_SHA256,
    OWNER_RELAY_OBSERVED_AT, OWNER_RELAY_EVIDENCE_STRENGTH, build_owner_relayed_response,
)
from lvef_multitask_analysis_modes import bind_approved_restricted_path, load_policy


class ReadinessError(ValueError):
    """Closed, non-sensitive evidence failure code."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ReadinessError(code)


def _bytes(path: Path, limit: int = 16 * 1024 * 1024) -> bytes:
    # Existing audit artifacts only; no source label tables, arrays or DICOM.
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
             and before.st_nlink == 1 and not before.st_mode & 0o022
             and 0 < before.st_size <= limit, "READINESS_CONTROL_FILE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        data = bytearray()
        while len(data) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    def identity(value: os.stat_result) -> tuple[int, ...]:
        return tuple(getattr(value, key) for key in (
            "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
            "st_size", "st_mtime_ns", "st_ctime_ns"))
    _require(identity(before) == identity(opened) == identity(after)
             == identity(path.lstat()) and len(data) == before.st_size,
             "READINESS_CONTROL_FILE_CHANGED")
    return bytes(data)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        _require(key not in result, "READINESS_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(data: bytes) -> dict[str, Any]:
    value = json.loads(data, object_pairs_hook=_pairs)
    _require(isinstance(value, dict), "READINESS_JSON_OBJECT_REQUIRED")
    return value


def inspect_clinician_packet(packet_dir: Path, review_rows_path: Path, *,
                             owner_relayed_response_path: Path | None = None) -> dict[str, Any]:
    """Bind a historical questionnaire to its metadata before reusing decisions."""
    packet_path = packet_dir / "clinical_metadata_clinician_signoff_restricted.md"
    response_path = packet_dir / "clinical_metadata_clinician_response_restricted.json"
    manifest_bytes = _bytes(packet_dir / "clinical_metadata_clinician_packet_manifest_restricted.json")
    manifest = _json(manifest_bytes)
    packet = _bytes(packet_path)
    review = _bytes(review_rows_path)
    commit = manifest.get("source_commit")
    _require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) is not None,
             "READINESS_CLINICIAN_SOURCE_COMMIT_INVALID")
    _require(manifest.get("audit") == "lvef_multitask_clinician_packet"
             and manifest.get("status") == "READY_FOR_HUMAN_SIGNOFF"
             and type(manifest.get("n_questions")) is int and manifest["n_questions"] == 8
             and manifest.get("question_ids") == list(CLINICAL_ISSUE_IDS)
             and manifest.get("packet_sha256") == _sha(packet),
             "READINESS_CLINICIAN_MANIFEST_MISMATCH")
    # Match build_command -> load_table exactly: empty cells become pandas NaN,
    # whose established markdown representation is "nan". Do not rewrite it.
    regenerated = build_packet(pd.read_csv(io.BytesIO(review), low_memory=False), commit)
    _require(regenerated.encode() == packet, "READINESS_CLINICIAN_METADATA_MISMATCH")
    response_bytes = _bytes(response_path)
    response = _json(response_bytes)
    if owner_relayed_response_path is not None:
        _require(owner_relayed_response_path != response_path, "READINESS_OWNER_RELAY_ORIGINAL_OVERWRITE_REFUSED")
        relay_bytes = _bytes(owner_relayed_response_path)
        relay = _json(relay_bytes)
        expected = build_owner_relayed_response(packet_path=packet_path,
            original_response_path=response_path, review_rows_path=review_rows_path)
        _require(json.dumps(relay, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False),
                 "READINESS_OWNER_RELAY_EVIDENCE_CHANGED")
        validation = validate_response(packet_path, relay)
        _require(validation["clinical_adjudication_complete"] is True and validation["n_validation_issues"] == 0,
                 "READINESS_OWNER_RELAY_VALIDATION_FAILED")
        _require(_bytes(packet_path) == packet and _bytes(response_path) == response_bytes
                 and _bytes(review_rows_path) == review and _bytes(owner_relayed_response_path) == relay_bytes,
                 "READINESS_CLINICIAN_CONTROL_CHANGED")
        return {"status": "PASS_OWNER_RELAYED_QUALIFIED_ECHO_REVIEW", "review_mode": OWNER_RELAY_MODE,
            "clinical_adjudication_complete": True, "human_signoff_complete": False,
            "n_questions": 8, "n_pending_questions": 0, "packet_sha256": _sha(packet),
            "response_sha256": _sha(relay_bytes), "original_response_sha256": _sha(response_bytes),
            "manifest_sha256": _sha(manifest_bytes), "review_rows_sha256": _sha(review),
            "owner_statement_sha256": OWNER_RELAY_SOURCE_SHA256, "input_audit_sha256": OWNER_RELAY_INPUT_SHA256,
            "communication_observed_at": OWNER_RELAY_OBSERVED_AT, "source_commit": commit,
            "evidence_strength": OWNER_RELAY_EVIDENCE_STRENGTH, "source_acquisition_conventions_verified": False,
            "expert_name_or_initials": None, "expert_direct_signature": None, "actual_expert_review_date": None,
            "qualification_reported_by_owner": "QUALIFIED_ECHOCARDIOGRAPHER", "agreement_reported_by_owner": True,
            "metadata_packet_regenerated_exactly": True,
            "questions": [{"issue_id": item["issue_id"], "selected_option": item["selected_option"],
                           "status": "DECIDED_OWNER_RELAYED", "accepted_nomenclature_synonyms": item["accepted_nomenclature_synonyms"]}
                          for item in relay["responses"]],
            "mitral_e_processing": relay["mitral_e_processing"], "n_validation_issues": 0,
            "reviewer_identity_exported": False, "restricted_metadata_exported": False}
    validation = validate_response(packet_path, response)
    _require(_bytes(packet_path) == packet and _bytes(response_path) == response_bytes,
             "READINESS_CLINICIAN_CONTROL_CHANGED")
    specs = {item["issue_id"]: item for item in CLINICAL_ISSUE_SPECS}
    supplied = response.get("responses")
    supplied = supplied if isinstance(supplied, list) else []
    # Choices are only promoted when the maintained eight-question validator passes.
    choices = {item["issue_id"]: item["selected_option"] for item in supplied
               if isinstance(item, dict) and item.get("issue_id") in specs
               and item.get("selected_option") in specs[item["issue_id"]]["options"]}
    complete = validation["signoff_complete"] is True
    return {
        "status": "PASS_CLINICIAN_SIGNOFF" if complete else "PENDING_HUMAN_SIGNOFF",
        "human_signoff_complete": complete,
        "n_questions": 8,
        "n_pending_questions": 0 if complete else 8,
        "packet_sha256": _sha(packet),
        "response_sha256": _sha(response_bytes),
        "manifest_sha256": _sha(manifest_bytes),
        "review_rows_sha256": _sha(review),
        "source_commit": commit,
        "metadata_packet_regenerated_exactly": True,
        "questions": [{"issue_id": issue, "selected_option": choices[issue] if complete else None,
                       "status": "DECIDED" if complete else "PENDING"}
                      for issue in CLINICAL_ISSUE_IDS],
        "n_validation_issues": validation["n_validation_issues"],
        "reviewer_identity_exported": False,
        "restricted_metadata_exported": False,
    }


TECHNICAL_OUTPUTS = {
    "restricted": {
        "technical_metadata_evidence_restricted.csv",
        "technical_metadata_train_completeness_restricted.csv",
        "technical_metadata_train_nonnumeric_values_restricted.csv",
        "technical_metadata_train_distributions_restricted.csv",
        "technical_metadata_pairwise_scale_diagnostics_restricted.csv",
    },
    "aggregate": {
        "technical_metadata_issue_summary.csv", "lvef_label_definition_counts.csv",
        "lvef_separate_label_authority.json", "technical_metadata_completeness_summary.json",
        "technical_metadata_safety_gate.json",
    },
}


def inspect_technical_packet(metadata_root: Path, *, expected_input_checksums: dict[str, str]) -> dict[str, Any]:
    """Replay prepared evidence; do not convert old pending rows to decisions."""
    aggregate = metadata_root / "aggregate" / "technical_metadata"
    manifest_bytes = _bytes(aggregate / "technical_metadata_manifest.json")
    manifest = _json(manifest_bytes)
    expected_keys = {"clinical_review_rows", "raw_canonical_mapping", "structured_measurements",
                     "selected_studies", "subject_split_map"}
    _require(set(expected_input_checksums) == expected_keys and all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        for value in expected_input_checksums.values()), "READINESS_INPUT_BINDINGS_REQUIRED")
    _require(manifest.get("audit") == "phase1e_technical_metadata"
             and manifest.get("status") == "COMPLETE_PENDING_SCIENTIFIC_ADJUDICATION"
             and manifest.get("input_checksums") == expected_input_checksums
             and manifest.get("confirmatory_performance_accessed") is False
             and manifest.get("model_outputs_read") is False,
             "READINESS_TECHNICAL_MANIFEST_MISMATCH")
    outputs = manifest.get("output_checksums")
    _require(isinstance(outputs, list), "READINESS_TECHNICAL_OUTPUTS_INVALID")
    seen: set[tuple[str, str]] = set()
    captured: dict[str, bytes] = {}
    for record in outputs:
        _require(isinstance(record, dict), "READINESS_TECHNICAL_OUTPUTS_INVALID")
        role, name = record.get("output_class"), record.get("relative_name")
        _require(role in TECHNICAL_OUTPUTS and name in TECHNICAL_OUTPUTS[role]
                 and (role, name) not in seen, "READINESS_TECHNICAL_OUTPUT_SET_MISMATCH")
        seen.add((role, name))
        data = _bytes(metadata_root / role / "technical_metadata" / name)
        _require(type(record.get("bytes")) is int and record["bytes"] == len(data)
                 and record.get("sha256") == _sha(data), "READINESS_TECHNICAL_OUTPUT_HASH_MISMATCH")
        captured[name] = data
    _require(seen == {(role, name) for role, names in TECHNICAL_OUTPUTS.items() for name in names},
             "READINESS_TECHNICAL_OUTPUT_SET_MISMATCH")
    gate = _json(captured["technical_metadata_safety_gate.json"])
    _require(gate.get("status") == "PASS" and gate.get("safety_gate_passed") is True
             and gate.get("confirmatory_performance_accessed") is False
             and gate.get("predictions_or_embeddings_read") is False,
             "READINESS_TECHNICAL_SAFETY_GATE_FAILED")
    rows = pd.read_csv(io.BytesIO(captured["technical_metadata_issue_summary.csv"]), keep_default_na=False)
    _require("issue_id" in rows and len(rows) == 9
             and set(rows["issue_id"]) == set(TECHNICAL_ISSUE_IDS),
             "READINESS_TECHNICAL_ISSUE_SET_MISMATCH")
    authority = _json(captured["lvef_separate_label_authority.json"])
    _require(authority.get("status") == "PASS"
             and authority.get("raw_target_match") == "EXACT_CASE_SENSITIVE_LVEF"
             and authority.get("aggregation") == "NUMERIC_MEDIAN_BY_SUBJECT_AND_MEASUREMENT_ID"
             and authority.get("mapping_row_required") is False
             and authority.get("synthetic_mapping_row_created") is False
             and authority.get("candidate_aliases_are_authority") is False,
             "READINESS_LVEF_AUTHORITY_MISMATCH")
    return {
        "status": "PASS_PREPARED_TECHNICAL_EVIDENCE",
        "manifest_sha256": _sha(manifest_bytes), "n_technical_questions": 9,
        "n_hash_verified_outputs": len(seen), "scientific_adjudication_complete": False,
        "technical_question_ids": list(TECHNICAL_ISSUE_IDS),
        "separate_exact_name_lvef_authority": True, "source_values_exported": False,
        "confirmatory_performance_accessed": False,
    }


UNIT_CLASSES = {
    "mm": "LENGTH_MM", "cm": "LENGTH_CM", "m": "LENGTH_M",
    "mmhg": "PRESSURE_MMHG", "cmh2o": "PRESSURE_CMH2O",
    "m/s": "VELOCITY_M_PER_S", "cm/s": "VELOCITY_CM_PER_S",
    "ml": "VOLUME_ML", "l": "VOLUME_L", "ml/m2": "INDEXED_VOLUME_ML_PER_M2",
    "m2": "AREA_M2", "cm2": "AREA_CM2", "%": "PERCENT", "ratio": "RATIO",
    "bpm": "RATE_BPM", "ms": "TIME_MS", "s": "TIME_S",
    "kg": "MASS_KG", "g": "MASS_G", "g/m2": "INDEXED_MASS_G_PER_M2",
    "unknown": "UNKNOWN",
}


def project_technical_metadata(metadata_root: Path) -> dict[str, Any]:
    """Two hash-bound audit CSVs to closed metadata classes, never raw strings.

    This is a review aid. Lexical method/region tags cannot authorize clinical
    equivalence, alias merging, scored membership or formula relationships.
    """
    aggregate = metadata_root / "aggregate/technical_metadata"
    manifest_bytes = _bytes(aggregate / "technical_metadata_manifest.json")
    manifest = _json(manifest_bytes)
    def read_csv(name: str) -> pd.DataFrame:
        entries = [row for row in manifest["output_checksums"]
                   if row.get("output_class") == "restricted" and row.get("relative_name") == name]
        _require(len(entries) == 1, "READINESS_TECHNICAL_OUTPUT_SET_MISMATCH")
        data = _bytes(metadata_root / "restricted/technical_metadata" / name)
        _require(entries[0].get("sha256") == _sha(data)
                 and entries[0].get("bytes") == len(data), "READINESS_TECHNICAL_OUTPUT_HASH_MISMATCH")
        return pd.read_csv(io.BytesIO(data), keep_default_na=False, low_memory=False)
    evidence = read_csv("technical_metadata_evidence_restricted.csv")
    completeness = read_csv("technical_metadata_train_completeness_restricted.csv")
    _require(not completeness["raw_name"].duplicated().any(), "READINESS_TECHNICAL_RAW_UNIVERSE_INVALID")
    by_raw = completeness.set_index("raw_name")
    names = sorted(set(evidence["raw_name"]))
    slots = {name: index + 1 for index, name in enumerate(names)}
    def unit_class(value: Any) -> str:
        return UNIT_CLASSES.get(normalize_unit(value), "UNRECOGNIZED")
    fields = []
    for name, group in evidence.groupby("raw_name", sort=True):
        _require(name in by_raw.index, "READINESS_TECHNICAL_RAW_UNIVERSE_INVALID")
        c = by_raw.loc[name]
        text = " ".join([name, *group["raw_description"].astype(str), *group["canonical_mapping"].astype(str)])
        train_units = [value for value in str(c["train_unit_tokens"]).split(";") if value]
        targets = sorted(set(group["allowlisted_target"]) & set(ALLOWED_TARGETS))
        issue_ids = sorted(set(group["issue_id"]))
        _require(set(issue_ids) <= set(TECHNICAL_ISSUE_IDS), "READINESS_TECHNICAL_ISSUE_SET_MISMATCH")
        fields.append({
            "field_slot": slots[name], "issue_ids": issue_ids, "allowlisted_targets": targets,
            "outside_target_allowlist": not targets,
            "raw_field_is_exact_lvef": name == "lvef",
            "native_unit_classes": sorted({unit_class(value) for value in group["native_unit"]}),
            "normalized_unit_classes": sorted({unit_class(value) for value in group["normalized_unit"]}),
            "train_unit_classes": sorted({unit_class(value) for value in train_units}) or ["UNKNOWN"],
            "n_train_unit_tokens": int(c["n_train_unit_tokens"]),
            "n_train_numeric_rows": int(c["n_train_numeric_rows"]),
            "n_train_nonnumeric_rows": int(c["n_train_nonnumeric_rows"]),
            "n_train_missing_value_rows": int(c["n_train_missing_value_rows"]),
            "mapping_is_ambiguous": str(c["mapping_is_ambiguous"]).lower() == "true",
            "method_word_present": bool(METHOD_PATTERN.search(text)),
            "indexed_word_present": bool(INDEXED_PATTERN.search(text)),
            "weight_word_present": bool(re.search(r"\b(weight|wt)\b", text, re.I)),
            "height_word_present": bool(re.search(r"\b(height|ht)\b", text, re.I)),
            "wall_motion_index_or_global_word_present": bool(re.search(r"\b(wmsi|global)\b|wall.?motion.{0,25}(score|index)", text, re.I)),
            "segment_region_word_present": bool(re.search(r"\b(basal|mid|apical|anterior|inferior|septal|lateral|inferolateral|anteroseptal)\b", text, re.I)),
        })
    return {"status": "PASS_TECHNICAL_METADATA_CLASS_PROJECTION", "manifest_sha256": _sha(manifest_bytes),
            "n_fields": len(fields), "fields": fields, "source_bulk_reads": 0,
            "raw_names_or_descriptions_exported": False, "patient_values_exported": False,
            "lexical_tags_authorize_clinical_relationships": False,
            "n_train_names_outside_complete_mapping": int((completeness["n_mapping_canonicals"] == 0).sum())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clinician-packet-dir", type=Path, required=True)
    parser.add_argument("--clinical-review-rows-csv", type=Path, required=True)
    parser.add_argument("--owner-relayed-response", type=Path)
    parser.add_argument("--safe-export-policy", type=Path,
                        default=Path(__file__).resolve().parents[1] / "configs/lvef_multitask_safe_export_policy.yaml")
    args = parser.parse_args()
    try:
        policy, _ = load_policy(args.safe_export_policy)
        packet = bind_approved_restricted_path(args.clinician_packet_dir, policy=policy,
                                               must_exist=True, expect="directory")
        rows = bind_approved_restricted_path(args.clinical_review_rows_csv, policy=policy,
                                             must_exist=True, expect="file")
        relay = None if args.owner_relayed_response is None else bind_approved_restricted_path(
            args.owner_relayed_response, policy=policy, must_exist=True, expect="file")
        result = inspect_clinician_packet(packet, rows, owner_relayed_response_path=relay)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("clinical_adjudication_complete", result["human_signoff_complete"]) else 2
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED_READINESS_EVIDENCE",
                          "failure_code": str(exc) if isinstance(exc, ReadinessError) else "READINESS_EVIDENCE_INVALID"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
