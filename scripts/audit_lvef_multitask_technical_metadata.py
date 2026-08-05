#!/usr/bin/env python3
"""Audit the nine technical metadata questions without reading model outputs.

The script is intended to run only on SCC.  It reads the restricted clinical
review rows produced by ``lvef_multitask_clinical_metadata.py`` and, when
available, training labels from the structured-measurement export.  Detailed
rows stay in the restricted output directory.  The aggregate directory never
contains raw names, descriptions, units, values, identifiers, or paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from lvef_multitask_audit_utils import (
    assert_aggregate_safe_columns,
    assert_aggregate_safe_json,
    load_table,
    require_restricted_path,
    resolve_column,
    run_guarded,
)
from lvef_multitask_clinical_metadata import (
    ALLOWED_TARGETS,
    DIMENSION_TARGETS,
    OUTPUT_COLUMNS as CLINICAL_REVIEW_OUTPUT_COLUMNS,
    VELOCITY_TARGETS,
    assert_metadata_only,
    build_review_rows,
    resolve_metadata_columns,
)
from lvef_multitask_analysis_modes import (
    bind_approved_restricted_path,
    load_policy as load_safe_export_policy,
)


TECHNICAL_ISSUE_IDS: tuple[str, ...] = (
    "BSA_FORMULA_WEIGHT_AVAILABILITY",
    "DIMENSION_CM_MM_UNITS",
    "LVEDV_LVESV_FIELDS",
    "LVEF_ALIASES",
    "LVEF_METHOD_MIXTURE",
    "LV_MASS_RWT_FIELDS",
    "MITRAL_EA_EEPRIME_RATIO_FIELDS",
    "VELOCITY_MPS_CMPS_UNITS",
    "WALL_MOTION_FIELDS",
)

EXPECTED_LVEF_SELECTED_PREIMAGING_COUNTS: dict[str, dict[str, int]] = {
    "all": {"n_observed": 2836, "n_equal_40": 103},
    "train": {"n_observed": 1998, "n_equal_40": 71},
    "val": {"n_observed": 411, "n_equal_40": 12},
    "test": {"n_observed": 427, "n_equal_40": 20},
}

ISSUE_CONSEQUENCES: dict[str, dict[str, str]] = {
    "BSA_FORMULA_WEIGHT_AVAILABILITY": {
        "residual_limitation": "BSA formula and weight-source availability require exact pipeline review.",
        "leakage_consequence": "A verified formula and its inputs form a deterministic dependency set.",
        "strict_panel_consequence": "Keep anthropometrics outside the primary echo-measurement macro panel.",
        "family_mask_consequence": "Mask verified BSA formula inputs and outputs together when one is targeted.",
        "pragmatic_panel_consequence": "Context may be permitted only as explicitly labeled metadata, never as an alias or exact formula shortcut.",
    },
    "DIMENSION_CM_MM_UNITS": {
        "residual_limitation": "Per-field native-to-normalized conversion requires source-unit and training-distribution agreement.",
        "leakage_consequence": "Unit-scaled copies are deterministic aliases and are prohibited predictors.",
        "strict_panel_consequence": "Exclude unresolved-unit targets and mask every verified unit-scaled alias.",
        "family_mask_consequence": "Use the same alias mask plus separately adjudicated clinical-family masks.",
        "pragmatic_panel_consequence": "Pragmatic completion still prohibits unit-scaled duplicates.",
    },
    "LVEDV_LVESV_FIELDS": {
        "residual_limitation": "Candidate presence, method, beat, and units require exact source review.",
        "leakage_consequence": "Method- and beat-matched LVEDV and LVESV deterministically reconstruct a volume-derived EF; equivalence to the project LVEF target additionally requires shared target provenance.",
        "strict_panel_consequence": "Hard-mask any verified LVEDV/LVESV pair for the LVEF anchor.",
        "family_mask_consequence": "Mask the adjudicated LV systolic-volumetric shortcut family.",
        "pragmatic_panel_consequence": "Exact formula reconstruction remains prohibited in same-report completion.",
    },
    "LVEF_ALIASES": {
        "residual_limitation": "Candidate EF exports require exact mapping and value-lineage adjudication.",
        "leakage_consequence": "Alternate exports of the same EF label are exact-target leakage.",
        "strict_panel_consequence": "Hard-mask every verified EF alias from LVEF prediction.",
        "family_mask_consequence": "Mask aliases plus the adjudicated LV systolic shortcut family.",
        "pragmatic_panel_consequence": "Aliases remain prohibited even in pragmatic report completion.",
    },
    "LVEF_METHOD_MIXTURE": {
        "residual_limitation": "Absent exact-target method lineage means method is unspecified and unstratifiable; it does not prove a mixed-method label.",
        "leakage_consequence": "Method-specific EF exports or formula inputs can recreate the target.",
        "strict_panel_consequence": "Retain LVEF as a separate anchor and fail closed on method-specific shortcuts.",
        "family_mask_consequence": "Mask all verified EF methods and near-deterministic LV systolic fields.",
        "pragmatic_panel_consequence": "Method mixture may be a stated label limitation but cannot justify exact-target predictors.",
    },
    "LV_MASS_RWT_FIELDS": {
        "residual_limitation": "Candidate mass, indexed mass, and RWT definitions and formula inputs require review.",
        "leakage_consequence": "LV mass, RWT, and indexed LV mass require separate formula sets; BSA is an ancestor only of indexed mass.",
        "strict_panel_consequence": "Mask verified formula ancestors and descendants; exclude unresolved constructs.",
        "family_mask_consequence": "Separate exact formula sets from broader LV-geometry correlations.",
        "pragmatic_panel_consequence": "Exact formula reconstruction is prohibited; nonalgebraic LV context requires explicit labeling.",
    },
    "MITRAL_EA_EEPRIME_RATIO_FIELDS": {
        "residual_limitation": "Candidate ratio identity, component definitions, and units require exact review.",
        "leakage_consequence": "E/A and E/e-prime are deterministic ratios when matched component fields are present.",
        "strict_panel_consequence": "Mask every verified ratio together with its exact numerator and denominator set.",
        "family_mask_consequence": "Treat formula sets separately from the wider mitral-diastolic correlation family.",
        "pragmatic_panel_consequence": "Exact ratio reconstruction remains prohibited.",
    },
    "VELOCITY_MPS_CMPS_UNITS": {
        "residual_limitation": "Per-field m/s versus cm/s conversion requires source-unit and train-only distribution agreement.",
        "leakage_consequence": "Velocity exports differing only by a factor of 100 are deterministic aliases.",
        "strict_panel_consequence": "Exclude unresolved-unit targets and mask verified converted aliases.",
        "family_mask_consequence": "Apply alias masks before separately defined Doppler clinical-family masks.",
        "pragmatic_panel_consequence": "Converted aliases remain prohibited in pragmatic completion.",
    },
    "WALL_MOTION_FIELDS": {
        "residual_limitation": "Candidate segment-level findings, wall-motion score/index, and global summaries require exact mapping, coding, and aggregation review.",
        "leakage_consequence": "Individual segment findings may be clinical correlates, whereas wall-motion score/index or global summaries may be deterministic aggregations or strong systolic shortcuts; global LV-function or EF-category exports may be target-derived.",
        "strict_panel_consequence": "Fail closed until segment-level findings are separated from wall-motion score/index, global summaries, and target-derived categories.",
        "family_mask_consequence": "Mask target-derived and adjudicated aggregate shortcuts; classify only verified segment-level findings in a distinct clinical-correlation family.",
        "pragmatic_panel_consequence": "Only adjudicated segment-level context may be considered for pragmatic use; wall-motion score/index, global summaries, and target-derived LV-function or EF categories remain prohibited until dependency review.",
    },
}


PAIRWISE_SCALES: dict[str, tuple[float, ...]] = {
    "DIMENSION_CM_MM_UNITS": (0.1, 1.0, 10.0),
    "VELOCITY_MPS_CMPS_UNITS": (0.01, 1.0, 100.0),
    "LVEF_ALIASES": (0.01, 1.0, 100.0),
}

REVIEW_REQUIRED_COLUMNS = frozenset(
    {
        "allowlisted_target",
        "raw_name",
        "raw_description",
        "canonical_mapping",
        "canonical_source",
        "native_unit",
        "normalized_unit",
        "candidate_lvef_alias_or_method",
        "candidate_lvef_method",
        "candidate_lvedv_lvesv",
        "candidate_wall_motion",
        "candidate_ratio",
        "candidate_lv_mass_or_rwt",
        "candidate_bsa_or_weight",
    }
)

RESTRICTED_EVIDENCE_COLUMNS: tuple[str, ...] = (
    "issue_id",
    "allowlisted_target",
    "raw_name",
    "raw_description",
    "canonical_mapping",
    "canonical_source",
    "native_unit",
    "normalized_unit",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_full_mapping_authority(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize the complete metadata-only mapping without filtering rows."""
    assert_metadata_only(frame)
    columns = resolve_metadata_columns(frame)
    raw_name = columns["raw_name"]
    canonical = columns["canonical_mapping"]
    assert raw_name is not None and canonical is not None
    normalized = pd.DataFrame(
        {
            "raw_name": frame[raw_name].fillna("").astype(str).str.strip(),
            "raw_description": (
                frame[columns["raw_description"]].fillna("").astype(str).str.strip()
                if columns["raw_description"] is not None
                else ""
            ),
            "canonical_mapping": frame[canonical].fillna("").astype(str).str.strip(),
            "canonical_source": (
                frame[columns["canonical_source"]].fillna("").astype(str).str.strip()
                if columns["canonical_source"] is not None
                else "UNKNOWN"
            ),
            "native_unit": (
                frame[columns["native_unit"]].fillna("").astype(str).str.strip()
                if columns["native_unit"] is not None
                else "UNKNOWN"
            ),
            "normalized_unit": (
                frame[columns["normalized_unit"]].fillna("").astype(str).str.strip()
                if columns["normalized_unit"] is not None
                else "UNKNOWN"
            ),
        }
    )
    if normalized["raw_name"].eq("").any() or normalized["canonical_mapping"].eq("").any():
        raise ValueError("Complete mapping contains a blank raw or canonical name")
    return normalized


def _stable_string_rows(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    selected = frame.loc[:, list(columns)].fillna("").astype(str)
    return selected.sort_values(list(columns), kind="stable").reset_index(drop=True)


def validate_full_mapping_authority(
    mapping: pd.DataFrame,
    review_rows: pd.DataFrame,
    *,
    expected_mapping_rows: int = 188,
    expected_review_rows: int = 67,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Bind the filtered packet to a complete, deterministic mapping authority."""
    normalized = normalize_full_mapping_authority(mapping)
    if len(normalized) != expected_mapping_rows:
        raise ValueError("Complete mapping authority does not have the expected row count")
    if len(review_rows) != expected_review_rows:
        raise ValueError("Clinical review packet does not have the expected row count")
    missing_columns = set(CLINICAL_REVIEW_OUTPUT_COLUMNS) - set(review_rows.columns)
    if missing_columns:
        raise ValueError(
            "Clinical review packet cannot be regenerated from the complete mapping; "
            f"missing columns: {sorted(missing_columns)}"
        )
    regenerated = build_review_rows(mapping, ALLOWED_TARGETS)
    expected = _stable_string_rows(regenerated, CLINICAL_REVIEW_OUTPUT_COLUMNS)
    observed = _stable_string_rows(review_rows, CLINICAL_REVIEW_OUTPUT_COLUMNS)
    if not expected.equals(observed):
        raise ValueError(
            "Clinical review packet is not the deterministic filtered view of the complete mapping"
        )
    canonical_names = set(normalized["canonical_mapping"].astype(str))
    expected_non_lvef_targets = set(ALLOWED_TARGETS) - {"lvef"}
    if not expected_non_lvef_targets.issubset(canonical_names):
        raise ValueError("Complete mapping does not cover all 29 non-LVEF requested targets")
    if "lvef" in canonical_names:
        raise ValueError("Historical complete mapping unexpectedly contains direct lvef authority")
    summary = {
        "status": "PASS_COMPLETE_MAPPING_AUTHORITY_RECONCILED",
        "n_source_mapping_rows": int(len(normalized)),
        "n_source_mapping_raw_names": int(normalized["raw_name"].nunique()),
        "n_regenerated_review_rows": int(len(regenerated)),
        "n_supplied_review_rows": int(len(review_rows)),
        "review_packet_exactly_regenerated": True,
        "non_lvef_requested_target_mapping_coverage_complete": True,
        "lvef_governed_by_separate_label_authority": True,
        "full_mapping_authority_supplied": True,
        "mapping_universe_completeness_proven": True,
        "absence_claim_authorized": True,
        "absence_claim_scope": "COMPLETE_RAW_TO_CANONICAL_MAPPING_ONLY_NOT_SOURCE_GENERATION_LINEAGE",
    }
    assert_aggregate_safe_json(summary)
    return normalized, summary


def truthy(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(str).str.strip().str.lower().isin(
        {"1", "true", "t", "yes", "y"}
    )


def issue_mask(rows: pd.DataFrame, issue_id: str) -> pd.Series:
    """Return the prespecified metadata rows relevant to one technical issue."""
    target = rows["allowlisted_target"].fillna("").astype(str)
    if issue_id == "BSA_FORMULA_WEIGHT_AVAILABILITY":
        return truthy(rows["candidate_bsa_or_weight"])
    if issue_id == "DIMENSION_CM_MM_UNITS":
        return target.isin(DIMENSION_TARGETS)
    if issue_id == "LVEDV_LVESV_FIELDS":
        return truthy(rows["candidate_lvedv_lvesv"])
    if issue_id == "LVEF_ALIASES":
        return truthy(rows["candidate_lvef_alias_or_method"]) & target.ne("lvef")
    if issue_id == "LVEF_METHOD_MIXTURE":
        return truthy(rows["candidate_lvef_method"]) | target.eq("lvef")
    if issue_id == "LV_MASS_RWT_FIELDS":
        return truthy(rows["candidate_lv_mass_or_rwt"])
    if issue_id == "MITRAL_EA_EEPRIME_RATIO_FIELDS":
        return truthy(rows["candidate_ratio"])
    if issue_id == "VELOCITY_MPS_CMPS_UNITS":
        return target.isin(VELOCITY_TARGETS)
    if issue_id == "WALL_MOTION_FIELDS":
        return truthy(rows["candidate_wall_motion"])
    raise ValueError(f"Unknown technical issue: {issue_id}")


def build_restricted_evidence(rows: pd.DataFrame) -> pd.DataFrame:
    missing = REVIEW_REQUIRED_COLUMNS - set(rows.columns)
    if missing:
        raise ValueError(f"Clinical review rows are missing columns: {sorted(missing)}")
    frames: list[pd.DataFrame] = []
    for issue_id in TECHNICAL_ISSUE_IDS:
        selected = rows.loc[issue_mask(rows, issue_id), RESTRICTED_EVIDENCE_COLUMNS[1:]].copy()
        selected.insert(0, "issue_id", issue_id)
        frames.append(selected)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=RESTRICTED_EVIDENCE_COLUMNS)


def build_issue_summary(
    evidence: pd.DataFrame,
    *,
    completeness: pd.DataFrame | None = None,
    paired_diagnostics: pd.DataFrame | None = None,
    mapping_authority_summary: dict[str, Any] | None = None,
    lvef_method_lineage_complete: bool = False,
) -> pd.DataFrame:
    full_mapping_authority_supplied = bool(
        mapping_authority_summary
        and mapping_authority_summary.get("full_mapping_authority_supplied") is True
    )
    mapping_universe_completeness_proven = bool(
        mapping_authority_summary
        and mapping_authority_summary.get("mapping_universe_completeness_proven") is True
    )
    absence_claim_authorized = bool(
        mapping_authority_summary
        and mapping_authority_summary.get("absence_claim_authorized") is True
    )
    records: list[dict[str, Any]] = []
    for issue_id in TECHNICAL_ISSUE_IDS:
        group = evidence[evidence["issue_id"] == issue_id]
        consequences = ISSUE_CONSEQUENCES[issue_id]
        issue_completeness = (
            completeness[
                completeness["raw_name"].isin(set(group["raw_name"].astype(str)))
            ]
            if completeness is not None and not completeness.empty
            else pd.DataFrame()
        )
        issue_pairs = (
            paired_diagnostics[paired_diagnostics["issue_id"] == issue_id]
            if paired_diagnostics is not None and not paired_diagnostics.empty
            else pd.DataFrame()
        )
        if issue_id == "LVEF_METHOD_MIXTURE" and not lvef_method_lineage_complete:
            disposition = "PENDING_COMPLETE_LVEF_METHOD_LINEAGE_REVIEW"
        elif issue_id == "WALL_MOTION_FIELDS":
            disposition = "PENDING_REGIONAL_VS_GLOBAL_SUBCLASSIFICATION"
        else:
            disposition = "PENDING_RESTRICTED_TECHNICAL_REVIEW"
        evidence_state = "RESTRICTED_REVIEW_PACKET_CANDIDATES_ONLY"
        if completeness is not None:
            evidence_state = "RESTRICTED_REVIEW_PACKET_PLUS_TRAIN_ONLY_PRESENCE_COUNTS"
        if full_mapping_authority_supplied:
            evidence_state = "COMPLETE_MAPPING_AUTHORITY_PLUS_TRAIN_ONLY_PRESENCE_COUNTS"
        if paired_diagnostics is not None:
            evidence_state += "_AND_PRESPECIFIED_PAIRED_DIAGNOSTICS"
        records.append(
            {
                "issue_id": issue_id,
                "review_type": "TECHNICAL_PIPELINE_REVIEW",
                "n_restricted_evidence_rows": int(len(group)),
                "n_allowlisted_targets": int(group["allowlisted_target"].replace("", pd.NA).nunique()),
                "n_candidate_source_fields": int(group["raw_name"].replace("", pd.NA).nunique()),
                "n_candidate_source_fields_present_train": (
                    int(issue_completeness.loc[issue_completeness["present_in_train"], "raw_name"].nunique())
                    if not issue_completeness.empty
                    else 0
                ),
                "n_pairwise_diagnostic_rows": int(len(issue_pairs)),
                "disposition": disposition,
                "confidence": "UNRESOLVED",
                "evidence_inspected": evidence_state,
                "full_mapping_authority_supplied": full_mapping_authority_supplied,
                "mapping_universe_completeness_proven": mapping_universe_completeness_proven,
                "absence_claim_authorized": absence_claim_authorized,
                "absence_claim_scope": (
                    mapping_authority_summary.get("absence_claim_scope", "NONE")
                    if mapping_authority_summary
                    else "NONE"
                ),
                "lvef_method_lineage_complete": lvef_method_lineage_complete,
                "train_candidate_presence_reconciled": completeness is not None,
                **consequences,
                "confirmatory_performance_accessed": False,
            }
        )
    result = pd.DataFrame(records)
    assert_aggregate_safe_columns(result)
    return result


def _resolve_structured_columns(frame: pd.DataFrame) -> dict[str, str | None]:
    return {
        "subject": str(resolve_column(frame, ("subject_id", "patient_id", "person_id"), required=True)),
        "measurement_id": str(
            resolve_column(frame, ("measurement_id", "measurement_report_id", "report_id"), required=True)
        ),
        "measurement": str(resolve_column(frame, ("measurement", "raw_name", "raw_measurement"), required=True)),
        "value": str(resolve_column(frame, ("result", "result_numeric", "measurement_value", "value"), required=True)),
        "unit": resolve_column(frame, ("unit", "native_unit", "raw_unit")),
        "description": resolve_column(
            frame,
            ("measurement_description", "raw_description", "description"),
        ),
    }


def _integer_key(series: pd.Series, label: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().any() or not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError(f"{label} contains a blank or nonnumeric key")
    if not np.equal(numeric.to_numpy(dtype=float), np.floor(numeric.to_numpy(dtype=float))).all():
        raise ValueError(f"{label} contains a fractional key")
    return numeric.astype("int64")


def _selected_keys(selected: pd.DataFrame) -> pd.DataFrame:
    subject = resolve_column(selected, ("subject_id", "patient_id", "person_id"), required=True)
    measurement_id = resolve_column(
        selected, ("measurement_id", "measurement_report_id", "report_id"), required=True
    )
    keys = selected[[subject, measurement_id]].copy()
    keys.columns = ["_subject", "_measurement_id"]
    keys["_subject"] = _integer_key(keys["_subject"], "selected subject")
    keys["_measurement_id"] = _integer_key(keys["_measurement_id"], "selected measurement")
    if keys.duplicated().any():
        raise ValueError("Selected-study linkage keys are not unique")
    if keys["_subject"].duplicated().any():
        raise ValueError("Selected-study authority is not one row per subject")
    return keys


def _split_rows(split_map: pd.DataFrame) -> pd.DataFrame:
    subject = resolve_column(split_map, ("subject_id", "patient_id", "person_id"), required=True)
    split = resolve_column(split_map, ("split", "data_split", "partition"), required=True)
    rows = split_map[[subject, split]].copy()
    rows.columns = ["_subject", "_split"]
    rows["_subject"] = _integer_key(rows["_subject"], "split-map subject")
    rows["_split"] = rows["_split"].astype(str).str.lower().replace({"validation": "val"})
    if rows["_subject"].duplicated().any():
        raise ValueError("Split map has duplicate subjects")
    unknown = sorted(set(rows["_split"]) - {"train", "val", "test"})
    if unknown:
        raise ValueError("Split map contains unsupported split labels")
    return rows


def prepare_selected_structured(
    structured: pd.DataFrame,
    selected: pd.DataFrame,
    split_map: pd.DataFrame,
) -> pd.DataFrame:
    columns = _resolve_structured_columns(structured)
    subject = str(columns["subject"])
    measurement_id = str(columns["measurement_id"])
    measurement = str(columns["measurement"])
    value = str(columns["value"])
    values = pd.DataFrame(
        {
            "_subject": structured[subject],
            "_measurement_id": structured[measurement_id],
            "_raw_name": structured[measurement].fillna("").astype(str),
            "_raw_value": structured[value],
            "_unit": (
                structured[str(columns["unit"])].fillna("").astype(str)
                if columns["unit"] is not None
                else ""
            ),
            "_raw_description": (
                structured[str(columns["description"])].fillna("").astype(str)
                if columns["description"] is not None
                else ""
            ),
        }
    )
    values["_subject"] = _integer_key(values["_subject"], "structured subject")
    values["_measurement_id"] = _integer_key(values["_measurement_id"], "structured measurement")
    values["_value"] = pd.to_numeric(values["_raw_value"], errors="coerce")
    values["_raw_value_present"] = (
        values["_raw_value"].notna()
        & values["_raw_value"].astype(str).str.strip().ne("")
    )
    values["_value_is_finite_numeric"] = values["_value"].notna()
    finite_mask = values["_value_is_finite_numeric"]
    values.loc[finite_mask, "_value_is_finite_numeric"] = np.isfinite(
        values.loc[finite_mask, "_value"].to_numpy(dtype=float)
    )
    selected_keys = _selected_keys(selected)
    split_rows = _split_rows(split_map)
    if set(selected_keys["_subject"]) != set(split_rows["_subject"]):
        raise ValueError("Split-map subjects do not exactly equal selected-study subjects")
    values = values.merge(selected_keys, on=["_subject", "_measurement_id"], validate="many_to_one")
    values = values.merge(split_rows, on="_subject", validate="many_to_one")
    return values


COMPLETENESS_COLUMNS: tuple[str, ...] = (
    "raw_name",
    "technical_issue_ids",
    "in_review_packet",
    "in_technical_evidence",
    "n_mapping_canonicals",
    "mapping_is_ambiguous",
    "present_in_train",
    "n_train_rows",
    "n_train_numeric_rows",
    "n_train_nonnumeric_rows",
    "n_train_missing_value_rows",
    "n_train_reports",
    "n_train_subjects",
    "n_train_unit_tokens",
    "train_unit_tokens",
)


def build_train_completeness(
    selected_values: pd.DataFrame,
    review_rows: pd.DataFrame,
    evidence: pd.DataFrame,
    full_mapping: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Reconcile the train raw-name universe to the complete mapping authority."""
    if "raw_name" not in review_rows.columns:
        raise ValueError("Clinical review rows do not contain raw_name")
    required_mapping_columns = {"raw_name", "canonical_mapping"}
    if not required_mapping_columns.issubset(full_mapping.columns):
        raise ValueError("Complete mapping authority is not normalized")
    train = selected_values[selected_values["_split"] == "train"].copy()
    train_names = {name for name in train["_raw_name"].astype(str) if name}
    packet_names = {
        name for name in review_rows["raw_name"].fillna("").astype(str) if name
    }
    evidence_names = {
        name for name in evidence["raw_name"].fillna("").astype(str) if name
    }
    mapping_names = {
        name for name in full_mapping["raw_name"].fillna("").astype(str) if name
    }
    mapping_by_name = (
        full_mapping[["raw_name", "canonical_mapping"]]
        .drop_duplicates()
        .groupby("raw_name")["canonical_mapping"]
        .agg(lambda values: tuple(sorted({str(value) for value in values})))
        .to_dict()
    )
    issues_by_name = (
        evidence[["raw_name", "issue_id"]]
        .drop_duplicates()
        .groupby("raw_name")["issue_id"]
        .agg(lambda values: ";".join(sorted({str(value) for value in values})))
        .to_dict()
    )
    records: list[dict[str, Any]] = []
    for raw_name in sorted(train_names | mapping_names):
        group = train[train["_raw_name"] == raw_name]
        present = not group.empty
        raw_present = group["_raw_value_present"] if present else pd.Series(dtype=bool)
        numeric = group["_value_is_finite_numeric"] if present else pd.Series(dtype=bool)
        units = sorted({str(value) for value in group["_unit"] if str(value).strip()})
        canonicals = mapping_by_name.get(raw_name, ())
        records.append(
            {
                "raw_name": raw_name,
                "technical_issue_ids": issues_by_name.get(raw_name, ""),
                "in_review_packet": raw_name in packet_names,
                "in_technical_evidence": raw_name in evidence_names,
                "n_mapping_canonicals": len(canonicals),
                "mapping_is_ambiguous": len(canonicals) > 1,
                "present_in_train": present,
                "n_train_rows": int(len(group)),
                "n_train_numeric_rows": int(numeric.sum()) if present else 0,
                "n_train_nonnumeric_rows": int((raw_present & ~numeric).sum()) if present else 0,
                "n_train_missing_value_rows": int((~raw_present).sum()) if present else 0,
                "n_train_reports": (
                    int(group[["_subject", "_measurement_id"]].drop_duplicates().shape[0])
                    if present
                    else 0
                ),
                "n_train_subjects": int(group["_subject"].nunique()) if present else 0,
                "n_train_unit_tokens": len(units),
                "train_unit_tokens": ";".join(units),
            }
        )
    result = pd.DataFrame(records, columns=COMPLETENESS_COLUMNS)
    summary = {
        "audit": "technical_metadata_train_completeness",
        "status": "PASS_COMPLETE_MAPPING_AUTHORITY_RECONCILED",
        "scope": "SELECTED_TRAIN_SPLIT_VS_COMPLETE_RAW_TO_CANONICAL_MAPPING",
        "n_train_raw_names": len(train_names),
        "n_complete_mapping_raw_names": len(mapping_names),
        "n_review_packet_raw_names": len(packet_names),
        "n_technical_evidence_raw_names": len(evidence_names),
        "n_train_names_in_review_packet": len(train_names & packet_names),
        "n_train_names_outside_review_packet": len(train_names - packet_names),
        "n_train_names_in_complete_mapping": len(train_names & mapping_names),
        "n_train_names_outside_complete_mapping": len(train_names - mapping_names),
        "n_review_packet_names_absent_train": len(packet_names - train_names),
        "n_ambiguous_review_packet_raw_names": int(result["mapping_is_ambiguous"].sum()),
        "full_mapping_authority_supplied": True,
        "mapping_universe_completeness_proven": True,
        "absence_claim_authorized": True,
        "absence_claim_scope": "COMPLETE_RAW_TO_CANONICAL_MAPPING_ONLY_NOT_SOURCE_GENERATION_LINEAGE",
        "validation_or_test_values_used": False,
    }
    assert_aggregate_safe_json(summary)
    return result, summary


NONNUMERIC_PROFILE_COLUMNS: tuple[str, ...] = (
    "technical_issue_ids",
    "raw_name",
    "unit",
    "raw_value",
    "n_train_rows",
    "n_train_reports",
    "n_train_subjects",
)


def build_train_nonnumeric_profile(
    selected_values: pd.DataFrame,
    evidence: pd.DataFrame,
) -> pd.DataFrame:
    """Retain train-only qualitative/code values in the restricted packet."""
    issues_by_name = (
        evidence[["raw_name", "issue_id"]]
        .drop_duplicates()
        .groupby("raw_name")["issue_id"]
        .agg(lambda values: ";".join(sorted({str(value) for value in values})))
        .to_dict()
    )
    candidate_names = set(issues_by_name)
    rows = selected_values[
        (selected_values["_split"] == "train")
        & selected_values["_raw_name"].isin(candidate_names)
        & selected_values["_raw_value_present"]
        & ~selected_values["_value_is_finite_numeric"]
    ].copy()
    if rows.empty:
        return pd.DataFrame(columns=NONNUMERIC_PROFILE_COLUMNS)
    rows["_raw_value_text"] = rows["_raw_value"].astype(str)
    rows["_report_key"] = list(zip(rows["_subject"], rows["_measurement_id"]))
    profile = (
        rows.groupby(
            ["_raw_name", "_unit", "_raw_value_text"],
            as_index=False,
            dropna=False,
        )
        .agg(
            n_train_rows=("_raw_value_text", "size"),
            n_train_reports=("_report_key", "nunique"),
            n_train_subjects=("_subject", "nunique"),
        )
        .rename(
            columns={
                "_raw_name": "raw_name",
                "_unit": "unit",
                "_raw_value_text": "raw_value",
            }
        )
    )
    profile.insert(
        0,
        "technical_issue_ids",
        profile["raw_name"].map(issues_by_name).fillna(""),
    )
    return profile.loc[:, NONNUMERIC_PROFILE_COLUMNS]


PAIRWISE_DIAGNOSTIC_COLUMNS: tuple[str, ...] = (
    "issue_id",
    "comparison_scope",
    "left_raw_name",
    "left_unit",
    "right_raw_name",
    "right_unit",
    "scale_applied_to_right",
    "n_same_report_pairs",
    "n_exact_equal",
    "n_numerically_equal",
    "fraction_exact_equal",
    "fraction_numerically_equal",
    "median_absolute_residual",
    "maximum_absolute_residual",
    "diagnostic_authority",
)


def _numeric_train_strata(selected_values: pd.DataFrame) -> pd.DataFrame:
    train = selected_values[
        (selected_values["_split"] == "train")
        & selected_values["_value_is_finite_numeric"]
    ].copy()
    if train.empty:
        return pd.DataFrame(
            columns=["_subject", "_measurement_id", "_raw_name", "_unit", "_value"]
        )
    return (
        train.groupby(
            ["_subject", "_measurement_id", "_raw_name", "_unit"],
            as_index=False,
            dropna=False,
        )["_value"]
        .median()
    )


def build_pairwise_scale_diagnostics(
    selected_values: pd.DataFrame,
    evidence: pd.DataFrame,
) -> pd.DataFrame:
    """Compare prespecified scale relationships on common train reports only.

    Diagnostics never adjudicate an alias.  They are limited to raw fields
    sharing one unambiguous canonical mapping, plus candidate EF exports versus
    the exact historical ``lvef`` target.
    """
    values = _numeric_train_strata(selected_values)
    if values.empty:
        return pd.DataFrame(columns=PAIRWISE_DIAGNOSTIC_COLUMNS)
    pair_specs: set[tuple[str, str, str, str, str, str]] = set()
    canonical_counts = evidence.groupby("raw_name")["canonical_mapping"].nunique()
    unambiguous_names = set(canonical_counts[canonical_counts == 1].index.astype(str))
    for issue_id in ("DIMENSION_CM_MM_UNITS", "VELOCITY_MPS_CMPS_UNITS"):
        issue = evidence[evidence["issue_id"] == issue_id]
        for canonical, group in issue.groupby("canonical_mapping", sort=True):
            raw_names = sorted(set(group["raw_name"].astype(str)) & unambiguous_names)
            if not canonical or not raw_names:
                continue
            observed = values[values["_raw_name"].isin(raw_names)]
            strata = sorted(
                set(zip(observed["_raw_name"].astype(str), observed["_unit"].astype(str)))
            )
            for left, right in combinations(strata, 2):
                pair_specs.add((issue_id, "SAME_CANONICAL_UNIT_OR_ALIAS", *left, *right))

    lvef_aliases = sorted(
        set(
            evidence.loc[evidence["issue_id"] == "LVEF_ALIASES", "raw_name"].astype(str)
        )
        & unambiguous_names
        - {"lvef"}
    )
    exact_lvef_strata = sorted(
        set(
            zip(
                values.loc[values["_raw_name"] == "lvef", "_raw_name"].astype(str),
                values.loc[values["_raw_name"] == "lvef", "_unit"].astype(str),
            )
        )
    )
    for alias in lvef_aliases:
        alias_rows = values[values["_raw_name"] == alias]
        alias_strata = sorted(
            set(zip(alias_rows["_raw_name"].astype(str), alias_rows["_unit"].astype(str)))
        )
        for left in alias_strata:
            for right in exact_lvef_strata:
                pair_specs.add(
                    ("LVEF_ALIASES", "CANDIDATE_TO_EXACT_LVEF", *left, *right)
                )

    records: list[dict[str, Any]] = []
    for issue_id, scope, left_name, left_unit, right_name, right_unit in sorted(pair_specs):
        left = values[
            (values["_raw_name"] == left_name) & (values["_unit"] == left_unit)
        ][["_subject", "_measurement_id", "_value"]].rename(
            columns={"_value": "_left"}
        )
        right = values[
            (values["_raw_name"] == right_name) & (values["_unit"] == right_unit)
        ][["_subject", "_measurement_id", "_value"]].rename(
            columns={"_value": "_right"}
        )
        paired = left.merge(right, on=["_subject", "_measurement_id"], validate="one_to_one")
        left_values = paired["_left"].to_numpy(dtype=float)
        right_values = paired["_right"].to_numpy(dtype=float)
        for scale in PAIRWISE_SCALES[issue_id]:
            scaled = right_values * scale
            residual = np.abs(left_values - scaled)
            exact = left_values == scaled
            numerical = np.isclose(left_values, scaled, rtol=1e-6, atol=1e-8)
            n_pairs = int(len(paired))
            records.append(
                {
                    "issue_id": issue_id,
                    "comparison_scope": scope,
                    "left_raw_name": left_name,
                    "left_unit": left_unit,
                    "right_raw_name": right_name,
                    "right_unit": right_unit,
                    "scale_applied_to_right": scale,
                    "n_same_report_pairs": n_pairs,
                    "n_exact_equal": int(exact.sum()),
                    "n_numerically_equal": int(numerical.sum()),
                    "fraction_exact_equal": float(exact.mean()) if n_pairs else np.nan,
                    "fraction_numerically_equal": float(numerical.mean()) if n_pairs else np.nan,
                    "median_absolute_residual": float(np.median(residual)) if n_pairs else np.nan,
                    "maximum_absolute_residual": float(np.max(residual)) if n_pairs else np.nan,
                    "diagnostic_authority": "DIAGNOSTIC_ONLY_NO_ALIAS_OR_UNIT_DECISION",
                }
            )
    return pd.DataFrame(records, columns=PAIRWISE_DIAGNOSTIC_COLUMNS)


def build_training_distributions(
    selected_values: pd.DataFrame,
    evidence: pd.DataFrame,
) -> pd.DataFrame:
    """Build restricted, train-only aggregate distributions for candidate fields."""
    issue_names = evidence[["issue_id", "raw_name"]].drop_duplicates()
    joined = selected_values[selected_values["_split"] == "train"].merge(
        issue_names,
        left_on="_raw_name",
        right_on="raw_name",
        validate="many_to_many",
    )
    records: list[dict[str, Any]] = []
    for (issue_id, raw_name), group in joined.groupby(["issue_id", "raw_name"], sort=True):
        numeric = group.loc[group["_value_is_finite_numeric"], "_value"].to_numpy(dtype=float)
        finite = numeric[np.isfinite(numeric)]
        quantiles = np.quantile(finite, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]) if len(finite) else [np.nan] * 7
        records.append(
            {
                "issue_id": issue_id,
                "raw_name": raw_name,
                "split": "train",
                "n_source_rows": int(len(group)),
                "n_numeric_rows": int(len(finite)),
                "n_nonnumeric_rows": int(
                    (group["_raw_value_present"] & ~group["_value_is_finite_numeric"]).sum()
                ),
                "n_missing_value_rows": int((~group["_raw_value_present"]).sum()),
                "n_finite_rows": int(len(finite)),
                "n_subjects": int(group["_subject"].nunique()),
                "minimum": float(np.min(finite)) if len(finite) else np.nan,
                "p01": float(quantiles[0]),
                "p05": float(quantiles[1]),
                "p25": float(quantiles[2]),
                "median": float(quantiles[3]),
                "p75": float(quantiles[4]),
                "p95": float(quantiles[5]),
                "p99": float(quantiles[6]),
                "maximum": float(np.max(finite)) if len(finite) else np.nan,
            }
        )
    return pd.DataFrame(records)


def build_lvef_authority(
    selected_values: pd.DataFrame,
    *,
    expected_counts: dict[str, dict[str, int]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if "_value_is_finite_numeric" in selected_values.columns:
        numeric_mask = selected_values["_value_is_finite_numeric"].astype(bool)
    else:
        numeric = pd.to_numeric(selected_values["_value"], errors="coerce")
        numeric_mask = numeric.notna() & np.isfinite(numeric.fillna(0).to_numpy(dtype=float))
    exact = selected_values[
        (selected_values["_raw_name"] == "lvef")
        & numeric_mask
    ].copy()
    labels = (
        exact.groupby(["_subject", "_measurement_id", "_split"], as_index=False)["_value"]
        .median()
        .rename(columns={"_value": "_lvef"})
    )
    counts: list[dict[str, Any]] = []
    for split in ("all", "train", "val", "test"):
        group = labels if split == "all" else labels[labels["_split"] == split]
        counts.append(
            {
                "scope": "selected_preimaging",
                "split": split,
                "n_observed": int(len(group)),
                "n_equal_40": int(group["_lvef"].eq(40.0).sum()),
                "n_lt_40": int(group["_lvef"].lt(40.0).sum()),
                "n_le_40": int(group["_lvef"].le(40.0).sum()),
                "n_lt_50": int(group["_lvef"].lt(50.0).sum()),
            }
        )
    count_frame = pd.DataFrame(counts)
    if set(expected_counts) != {"all", "train", "val", "test"}:
        raise ValueError("Expected LVEF reconciliation counts do not cover all split scopes")
    for split, expected in expected_counts.items():
        row = count_frame[count_frame["split"] == split]
        if len(row) != 1:
            raise ValueError("LVEF reconciliation produced a missing or duplicate split row")
        observed = row.iloc[0]
        for metric in ("n_observed", "n_equal_40"):
            if metric not in expected:
                raise ValueError(f"Expected LVEF reconciliation omits {metric} for {split}")
            if int(observed[metric]) != int(expected[metric]):
                raise ValueError(
                    f"LVEF selected-preimaging reconciliation failed for {split} {metric}"
                )
    authority = {
        "audit": "lvef_separate_label_authority",
        "status": "PASS",
        "raw_target_match": "EXACT_CASE_SENSITIVE_LVEF",
        "aggregation": "NUMERIC_MEDIAN_BY_SUBJECT_AND_MEASUREMENT_ID",
        "unit": "ANALYTICAL_EF_PERCENTAGE_POINT_SCALE_NATIVE_UNIT_UNVERIFIED",
        "analysis_scale": "EF_PERCENTAGE_POINTS",
        "native_unit_status": "NOT_ESTABLISHED_BY_MAPPING_AUTHORITY",
        "historical_primary_binary_endpoint": "lvef < 40",
        "secondary_binary_sensitivities": ["lvef <= 40", "lvef < 50"],
        "mapping_row_required": False,
        "synthetic_mapping_row_created": False,
        "method_mixture_resolved": False,
        "candidate_aliases_are_authority": False,
        "selected_preimaging_denominators_reconciled": True,
        "exact_40_counts_reconciled": True,
        "model_outputs_read": False,
        "confirmatory_performance_accessed": False,
    }
    assert_aggregate_safe_json(authority)
    return count_frame, authority


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clinical-review-rows-csv", type=Path, required=True)
    parser.add_argument("--raw-canonical-mapping-csv", type=Path, required=True)
    parser.add_argument(
        "--expected-raw-canonical-mapping-sha256",
        required=True,
    )
    parser.add_argument("--structured-measurements-csv", type=Path, required=True)
    parser.add_argument("--selected-studies-csv", type=Path, required=True)
    parser.add_argument("--subject-split-map-csv", type=Path, required=True)
    parser.add_argument(
        "--safe-export-policy",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "configs"
        / "lvef_multitask_safe_export_policy.yaml",
    )
    parser.add_argument("--expected-selected-studies", type=int, default=4530)
    parser.add_argument("--expected-raw-canonical-mapping-rows", type=int, default=188)
    parser.add_argument("--expected-clinical-review-rows", type=int, default=67)
    parser.add_argument("--restricted-output-dir", type=Path, required=True)
    parser.add_argument("--aggregate-output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    safe_policy, _ = load_safe_export_policy(args.safe_export_policy)
    input_paths = [
        bind_approved_restricted_path(
            path,
            policy=safe_policy,
            must_exist=True,
            expect="file",
        )
        for path in (
            args.clinical_review_rows_csv,
            args.raw_canonical_mapping_csv,
            args.structured_measurements_csv,
            args.selected_studies_csv,
            args.subject_split_map_csv,
        )
    ]
    (
        clinical_review_rows,
        raw_canonical_mapping_path,
        structured_measurements,
        selected_studies_path,
        split_map_path,
    ) = input_paths
    expected_mapping_sha256 = str(args.expected_raw_canonical_mapping_sha256).strip().lower()
    if len(expected_mapping_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in expected_mapping_sha256
    ):
        raise ValueError("Expected raw-to-canonical mapping SHA-256 is malformed")
    if sha256_file(raw_canonical_mapping_path) != expected_mapping_sha256:
        raise ValueError("Raw-to-canonical mapping checksum does not match authority")
    restricted_dir = bind_approved_restricted_path(
        args.restricted_output_dir,
        policy=safe_policy,
        must_exist=False,
        expect="directory",
        root_kind="direct",
        create=True,
    )
    aggregate_dir = bind_approved_restricted_path(
        args.aggregate_output_dir,
        policy=safe_policy,
        must_exist=False,
        expect="directory",
        root_kind="staging",
        create=True,
    )
    if restricted_dir == aggregate_dir:
        raise ValueError("Restricted and aggregate output directories must differ")
    if any(restricted_dir.iterdir()) or any(aggregate_dir.iterdir()):
        raise ValueError("Technical-metadata output directories must be empty")

    review_rows = load_table(clinical_review_rows)
    full_mapping, mapping_authority_summary = validate_full_mapping_authority(
        load_table(raw_canonical_mapping_path),
        review_rows,
        expected_mapping_rows=args.expected_raw_canonical_mapping_rows,
        expected_review_rows=args.expected_clinical_review_rows,
    )
    evidence = build_restricted_evidence(review_rows)
    selected_studies = load_table(selected_studies_path)
    if len(selected_studies) != args.expected_selected_studies:
        raise ValueError("Selected-study authority does not have the expected row count")
    selected_values = prepare_selected_structured(
        load_table(structured_measurements),
        selected_studies,
        load_table(split_map_path),
    )
    completeness, completeness_summary = build_train_completeness(
        selected_values,
        review_rows,
        evidence,
        full_mapping,
    )
    completeness_summary.update(
        {
            "n_source_mapping_rows": mapping_authority_summary["n_source_mapping_rows"],
            "n_regenerated_review_rows": mapping_authority_summary[
                "n_regenerated_review_rows"
            ],
            "review_packet_exactly_regenerated": mapping_authority_summary[
                "review_packet_exactly_regenerated"
            ],
            "non_lvef_requested_target_mapping_coverage_complete": mapping_authority_summary[
                "non_lvef_requested_target_mapping_coverage_complete"
            ],
            "lvef_governed_by_separate_label_authority": True,
        }
    )
    assert_aggregate_safe_json(completeness_summary)
    nonnumeric_profile = build_train_nonnumeric_profile(selected_values, evidence)
    paired_diagnostics = build_pairwise_scale_diagnostics(selected_values, evidence)
    issue_summary = build_issue_summary(
        evidence,
        completeness=completeness,
        paired_diagnostics=paired_diagnostics,
        mapping_authority_summary=mapping_authority_summary,
        lvef_method_lineage_complete=False,
    )
    distributions = build_training_distributions(selected_values, evidence)
    exact_40, lvef_authority = build_lvef_authority(
        selected_values,
        expected_counts=EXPECTED_LVEF_SELECTED_PREIMAGING_COUNTS,
    )

    restricted_paths = {
        "technical_metadata_evidence_restricted.csv": evidence,
        "technical_metadata_train_completeness_restricted.csv": completeness,
        "technical_metadata_train_nonnumeric_values_restricted.csv": nonnumeric_profile,
        "technical_metadata_train_distributions_restricted.csv": distributions,
        "technical_metadata_pairwise_scale_diagnostics_restricted.csv": paired_diagnostics,
    }
    for name, frame in restricted_paths.items():
        frame.to_csv(restricted_dir / name, index=False)

    assert_aggregate_safe_columns(exact_40)
    issue_summary.to_csv(aggregate_dir / "technical_metadata_issue_summary.csv", index=False)
    exact_40.to_csv(aggregate_dir / "lvef_label_definition_counts.csv", index=False)
    (aggregate_dir / "lvef_separate_label_authority.json").write_text(
        json.dumps(lvef_authority, indent=2, sort_keys=True) + "\n"
    )
    (aggregate_dir / "technical_metadata_completeness_summary.json").write_text(
        json.dumps(completeness_summary, indent=2, sort_keys=True) + "\n"
    )

    gate = {
        "audit": "phase1e_technical_metadata_safety_gate",
        "status": "PASS",
        "safety_gate_passed": True,
        "n_technical_issues": len(TECHNICAL_ISSUE_IDS),
        "all_dispositions_performance_independent": True,
        "training_only_distributions_restricted": True,
        "training_only_completeness_reconciliation_restricted": True,
        "training_only_nonnumeric_values_restricted": True,
        "training_only_paired_diagnostics_restricted": True,
        "full_mapping_universe_completeness_proven": True,
        "mapping_absence_claims_authorized": True,
        "mapping_absence_claim_scope": mapping_authority_summary["absence_claim_scope"],
        "lvef_method_lineage_complete": False,
        "raw_names_descriptions_units_exported": False,
        "row_values_or_identifiers_exported": False,
        "predictions_or_embeddings_read": False,
        "confirmatory_performance_accessed": False,
    }
    assert_aggregate_safe_json(gate)
    (aggregate_dir / "technical_metadata_safety_gate.json").write_text(
        json.dumps(gate, indent=2, sort_keys=True) + "\n"
    )

    preserved_outputs = sorted(
        [path for path in restricted_dir.iterdir() if path.is_file()]
        + [path for path in aggregate_dir.iterdir() if path.is_file()],
        key=lambda path: (path.parent.name, path.name),
    )

    manifest = {
        "audit": "phase1e_technical_metadata",
        "status": "COMPLETE_PENDING_SCIENTIFIC_ADJUDICATION",
        "input_checksums": {
            "clinical_review_rows": sha256_file(clinical_review_rows),
            "raw_canonical_mapping": sha256_file(raw_canonical_mapping_path),
            "structured_measurements": sha256_file(structured_measurements),
            "selected_studies": sha256_file(selected_studies_path),
            "subject_split_map": sha256_file(split_map_path),
        },
        "n_technical_issues": len(TECHNICAL_ISSUE_IDS),
        "n_restricted_evidence_rows": int(len(evidence)),
        "n_train_completeness_rows": int(len(completeness)),
        "n_train_nonnumeric_profile_rows": int(len(nonnumeric_profile)),
        "n_train_distribution_rows": int(len(distributions)),
        "n_pairwise_diagnostic_rows": int(len(paired_diagnostics)),
        "mapping_universe_completeness_proven": True,
        "mapping_absence_claims_authorized": True,
        "mapping_absence_claim_scope": mapping_authority_summary["absence_claim_scope"],
        "lvef_method_lineage_complete": False,
        "lvef_authority_status": lvef_authority["status"],
        "output_checksums": [
            {
                "output_class": "restricted" if path.parent == restricted_dir else "aggregate",
                "relative_name": path.name,
                "bytes": int(path.stat().st_size),
                "sha256": sha256_file(path),
            }
            for path in preserved_outputs
        ],
        "restricted_output_export_authorized": False,
        "model_outputs_read": False,
        "confirmatory_performance_accessed": False,
    }
    assert_aggregate_safe_json(manifest)
    (aggregate_dir / "technical_metadata_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "n_technical_issues": len(TECHNICAL_ISSUE_IDS),
                "lvef_authority_status": lvef_authority["status"],
                "restricted_metadata_printed": False,
                "confirmatory_performance_accessed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
