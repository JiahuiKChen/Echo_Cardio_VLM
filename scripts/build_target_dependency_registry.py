#!/usr/bin/env python3
"""Build a provisional target/predictor dependency registry for clinical review.

Unknown relationships fail closed as UNCERTAIN_REQUIRES_CLINICAL_REVIEW. The
script does not use model performance to adjudicate clinical relationships.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import (
    load_table,
    require_restricted_path,
    resolve_column,
    repository_root,
    run_guarded,
    write_aggregate_csv,
)


RELATIONSHIP_CATEGORIES = {
    "DIRECT_TARGET",
    "SYNONYM_OR_DUPLICATE",
    "DETERMINISTIC_DERIVATIVE",
    "NEAR_DETERMINISTIC_CLINICAL_DERIVATIVE",
    "SAME_REPORT_CORRELATE",
    "INDEPENDENT_STRUCTURED_PREDICTOR",
    "UNCERTAIN_REQUIRES_CLINICAL_REVIEW",
}

HISTORICAL_TASKS = [
    "fs",
    "tr_mmhg",
    "tricuspid_regurgitant_peak_velocity",
    "left_ventricular_end_systolic_diameter",
    "left_ventricular_end_diastolic_diameter",
    "body_surface_area",
    "height_cm",
    "av_pk_vel",
    "sept_e_prime",
    "la_4ch_length",
    "ra_length",
    "la_dimen",
    "sinus_diam",
    "resting_hr",
    "inf_lat_thickness",
    "mv_peak_a",
    "mv_peak_e",
    "septal_thickness",
    "lvot_vti",
    "ascending_aorta_diameter",
    "rv_diam",
    "lvot_diam",
    "mitral_e_velocity",
    "tricuspid_annular_plane_systolic_excursion",
    "arch_diam",
    "resting_dbp",
    "resting_sbp",
    "ivc_diam",
    "lat_e_prime",
]

PRIMARY_ANCHOR_TARGETS = ["lvef"]

STRICT_TARGETS = {
    "tricuspid_regurgitant_peak_velocity",
    "left_ventricular_end_systolic_diameter",
    "left_ventricular_end_diastolic_diameter",
    "av_pk_vel",
    "sept_e_prime",
    "la_4ch_length",
    "ra_length",
    "la_dimen",
    "sinus_diam",
    "inf_lat_thickness",
    "mv_peak_a",
    "mv_peak_e",
    "septal_thickness",
    "lvot_vti",
    "ascending_aorta_diameter",
    "rv_diam",
    "lvot_diam",
    "tricuspid_annular_plane_systolic_excursion",
    "arch_diam",
    "ivc_diam",
    "lat_e_prime",
}
PRAGMATIC_TARGETS = STRICT_TARGETS | {"body_surface_area", "height_cm", "resting_hr", "resting_dbp", "resting_sbp"}

SYNONYM_PAIRS = {
    frozenset({"mv_peak_e", "mitral_e_velocity"}): "Apparent duplicate mitral inflow E-wave velocity exports; raw descriptions require confirmation.",
}
DETERMINISTIC_PAIRS = {
    frozenset({"fs", "left_ventricular_end_systolic_diameter"}): "Fractional shortening uses LV end-systolic and end-diastolic diameters.",
    frozenset({"fs", "left_ventricular_end_diastolic_diameter"}): "Fractional shortening uses LV end-systolic and end-diastolic diameters.",
    frozenset({"tr_mmhg", "tricuspid_regurgitant_peak_velocity"}): "TR peak gradient is conventionally derived from peak TR velocity using 4v^2 after unit reconciliation.",
}
NEAR_DETERMINISTIC_PAIRS = {
    frozenset({"body_surface_area", "height_cm"}): "Height is an input to standard BSA formulas; weight is also required.",
    frozenset({"lvot_vti", "lvot_diam"}): "LVOT diameter determines cross-sectional area; area times LVOT VTI determines stroke volume.",
    frozenset({"lvot_vti", "resting_hr"}): "LVOT VTI contributes to stroke volume and heart rate converts stroke volume to cardiac output.",
    frozenset({"lvot_diam", "resting_hr"}): "LVOT geometry and heart rate are components of cardiac output derivations.",
    frozenset({"septal_thickness", "inf_lat_thickness"}): "Both wall thicknesses, with LV diameter, contribute to LV mass formulas.",
    frozenset({"septal_thickness", "left_ventricular_end_diastolic_diameter"}): "Septal thickness and LV diastolic diameter contribute to LV mass formulas.",
    frozenset({"inf_lat_thickness", "left_ventricular_end_diastolic_diameter"}): "Posterior/inferolateral thickness and LV diastolic diameter contribute to LV mass formulas.",
}

FAMILIES = {
    "lv_systolic_geometry": {"lvef", "fs", "left_ventricular_end_systolic_diameter", "left_ventricular_end_diastolic_diameter"},
    "tr_pulmonary_pressure": {"tr_mmhg", "tricuspid_regurgitant_peak_velocity", "ivc_diam"},
    "anthropometrics": {"body_surface_area", "height_cm"},
    "aortic_valve_flow": {"av_pk_vel", "lvot_vti", "lvot_diam"},
    "mitral_diastolic": {"sept_e_prime", "lat_e_prime", "mv_peak_a", "mv_peak_e", "mitral_e_velocity"},
    "atrial_geometry": {"la_4ch_length", "la_dimen", "ra_length"},
    "aortic_geometry": {"sinus_diam", "ascending_aorta_diameter", "arch_diam"},
    "lv_wall_geometry": {"septal_thickness", "inf_lat_thickness", "left_ventricular_end_diastolic_diameter"},
    "rv_structure_function": {"rv_diam", "tricuspid_annular_plane_systolic_excursion", "tricuspid_regurgitant_peak_velocity", "ivc_diam"},
    "hemodynamic_context": {"resting_hr", "resting_dbp", "resting_sbp", "body_surface_area"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-task-csv", type=Path)
    parser.add_argument("--mapping-csv", type=Path)
    parser.add_argument("--task-metadata-csv", type=Path)
    parser.add_argument("--output-registry-csv", type=Path, required=True)
    parser.add_argument("--output-evidence-csv", type=Path, required=True)
    return parser.parse_args()


def normalize_task(value: Any) -> str:
    text = str(value).strip()
    if text.startswith("task__"):
        text = text[len("task__") :]
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9]+", "_", text)).strip("_").lower()


def historical_tasks(path: Path | None) -> list[str]:
    if path is None:
        return PRIMARY_ANCHOR_TARGETS + HISTORICAL_TASKS.copy()
    if not path.exists():
        raise FileNotFoundError("Explicit historical task input is missing")
    frame = load_table(path)
    task_col = resolve_column(frame, ("task_col", "task", "canonical_measurement"), required=True)
    assert task_col is not None
    source_values = frame[task_col].astype("string")
    if source_values.isna().any():
        raise ValueError("Explicit historical task input contains missing task names")
    tasks = [normalize_task(value) for value in source_values.tolist()]
    if any(not task for task in tasks):
        raise ValueError("Explicit historical task input contains blank normalized task names")
    if len(tasks) != len(set(tasks)):
        raise ValueError("Explicit historical task input contains duplicate normalized targets")
    return list(dict.fromkeys(PRIMARY_ANCHOR_TARGETS + tasks))


def candidate_predictors(tasks: list[str], mapping_path: Path | None, metadata_path: Path | None) -> pd.DataFrame:
    rows = [{"predictor": task, "raw_name": task, "canonical_name": task, "unit": "UNKNOWN"} for task in tasks]
    if mapping_path is not None:
        if not mapping_path.exists():
            raise FileNotFoundError("Explicit raw-to-canonical mapping input is missing")
        mapping = load_table(mapping_path)
        raw_col = resolve_column(mapping, ("measurement", "raw_name", "raw_measurement"), required=True)
        canonical_col = resolve_column(mapping, ("canonical_measurement", "canonical_name", "task"), required=True)
        unit_col = resolve_column(mapping, ("unit", "preferred_unit", "recommended_canonical_unit"))
        assert raw_col is not None and canonical_col is not None
        for _, row in mapping.iterrows():
            canonical = normalize_task(row[canonical_col])
            raw = str(row[raw_col])
            rows.append(
                {
                    "predictor": canonical,
                    "raw_name": raw,
                    "canonical_name": canonical,
                    "unit": str(row[unit_col]) if unit_col and pd.notna(row[unit_col]) else "UNKNOWN",
                }
            )
    if metadata_path is not None:
        if not metadata_path.exists():
            raise FileNotFoundError("Explicit task metadata input is missing")
        metadata = load_table(metadata_path)
        canonical_col = resolve_column(
            metadata,
            ("canonical_measurement", "canonical_name", "task_col", "task"),
            required=True,
            label="task metadata canonical measurement",
        )
        unit_col = resolve_column(metadata, ("recommended_canonical_unit", "preferred_unit", "unit"))
        assert canonical_col is not None
        for _, row in metadata.iterrows():
            canonical = normalize_task(row[canonical_col])
            rows.append(
                {
                    "predictor": canonical,
                    "raw_name": canonical,
                    "canonical_name": canonical,
                    "unit": str(row[unit_col]) if unit_col and pd.notna(row[unit_col]) else "UNKNOWN",
                }
            )
    return pd.DataFrame(rows).drop_duplicates(["raw_name", "canonical_name", "unit"]).reset_index(drop=True)


def shared_family(left: str, right: str) -> str | None:
    for family, members in FAMILIES.items():
        if left in members and right in members:
            return family
    return None


def classify_relationship(target: str, predictor: str) -> tuple[str, str, str]:
    if target == predictor:
        return "DIRECT_TARGET", "Predictor canonical name equals the target canonical name.", "SCHEMA_IDENTITY"
    if target == "lvef" and predictor in {"ef", "ejection_fraction", "left_ventricular_ejection_fraction"}:
        return "SYNONYM_OR_DUPLICATE", "Predictor name denotes left ventricular ejection fraction.", "NAME_PATTERN_PENDING_RAW_ADJUDICATION"
    if target == "lvef" and predictor in {"fs", "left_ventricular_end_systolic_diameter", "left_ventricular_end_diastolic_diameter", "lvedv", "lvesv", "left_ventricular_end_diastolic_volume", "left_ventricular_end_systolic_volume"}:
        return "NEAR_DETERMINISTIC_CLINICAL_DERIVATIVE", "LVEF is mathematically determined by paired LV volumes; fractional shortening and paired LV dimensions are strong method-dependent systolic-function derivatives/proxies.", "STANDARD_LV_SYSTOLIC_FORMULAS_PENDING_CLINICAL_CITATION"
    pair = frozenset({target, predictor})
    if pair in SYNONYM_PAIRS:
        return "SYNONYM_OR_DUPLICATE", SYNONYM_PAIRS[pair], "PHASE0_REPO_AUDIT_PENDING_RAW_ADJUDICATION"
    if pair in DETERMINISTIC_PAIRS:
        return "DETERMINISTIC_DERIVATIVE", DETERMINISTIC_PAIRS[pair], "STANDARD_ECHOCARDIOGRAPHIC_FORMULA_PENDING_CLINICAL_CITATION"
    if pair in NEAR_DETERMINISTIC_PAIRS:
        return "NEAR_DETERMINISTIC_CLINICAL_DERIVATIVE", NEAR_DETERMINISTIC_PAIRS[pair], "STANDARD_CLINICAL_FORMULA_PENDING_CLINICAL_CITATION"
    family = shared_family(target, predictor)
    if family:
        return "SAME_REPORT_CORRELATE", f"Both fields are provisionally assigned to the {family} family; exact permissibility requires clinical review.", "PROVISIONAL_FAMILY_TAXONOMY"
    return "UNCERTAIN_REQUIRES_CLINICAL_REVIEW", "No relationship has been adjudicated from authoritative raw metadata and clinical evidence.", "PENDING_OPENEVIDENCE_AND_CLINICAL_REVIEW"


def predictor_allowed(category: str, mode: str) -> bool:
    if category in {"DIRECT_TARGET", "SYNONYM_OR_DUPLICATE", "DETERMINISTIC_DERIVATIVE", "NEAR_DETERMINISTIC_CLINICAL_DERIVATIVE"}:
        return False
    if category == "SAME_REPORT_CORRELATE":
        return mode == "pragmatic"
    if category == "INDEPENDENT_STRUCTURED_PREDICTOR":
        return True
    return False  # uncertain relationships fail closed until adjudicated


def evidence_row(task: str) -> dict[str, Any]:
    family_names = [family for family, members in FAMILIES.items() if task in members]
    if task == "lvef":
        disposition = "PRIMARY_LVEF_ANCHOR"
    elif task in {"fs", "tr_mmhg"}:
        disposition = "DETERMINISTIC_CALCULATION_NOT_ML_MACRO"
    elif task == "mitral_e_velocity":
        disposition = "MERGE_WITH_MV_PEAK_E"
    elif task in STRICT_TARGETS:
        disposition = "STRICT_AND_PRAGMATIC_TARGET"
    elif task in PRAGMATIC_TARGETS:
        disposition = "PRAGMATIC_CONTEXT_TARGET_ONLY"
    else:
        disposition = "PENDING_REVIEW"
    return {
        "target": task,
        "historical_exact_target_excluded": True,
        "provisional_families": ";".join(family_names) or "UNASSIGNED",
        "provisional_disposition": disposition,
        "strict_target_included": task in STRICT_TARGETS,
        "pragmatic_target_included": task in PRAGMATIC_TARGETS,
        "primary_anchor_target": task in PRIMARY_ANCHOR_TARGETS,
        "raw_synonyms_complete": False,
        "formula_review_complete": False,
        "clinical_review_status": "PENDING_OPENEVIDENCE_AND_CLINICIAN_ADJUDICATION",
    }


def main() -> int:
    args = parse_args()
    supplied_inputs = (args.historical_task_csv, args.mapping_csv, args.task_metadata_csv)
    n_missing_inputs = sum(path is not None and not path.is_file() for path in supplied_inputs)
    if n_missing_inputs:
        print(json.dumps({"status": "BLOCKED_MISSING_INPUT", "n_missing_inputs": n_missing_inputs}))
        return 2
    if args.mapping_csv is not None:
        output_parent = args.output_registry_csv.expanduser().resolve().parent
        root = repository_root()
        if output_parent == root or root in output_parent.parents:
            raise ValueError(
                "A registry expanded from raw SCC mappings is restricted and cannot be written inside the repository."
            )
        require_restricted_path(output_parent)
    tasks = historical_tasks(args.historical_task_csv)
    predictors = candidate_predictors(tasks, args.mapping_csv, args.task_metadata_csv)
    registry_rows: list[dict[str, Any]] = []
    for target in tasks:
        for _, candidate in predictors.iterrows():
            predictor = str(candidate["canonical_name"])
            category, rationale, source = classify_relationship(target, predictor)
            if category not in RELATIONSHIP_CATEGORIES:
                raise RuntimeError(f"Unexpected category: {category}")
            registry_rows.append(
                {
                    "target": target,
                    "predictor": predictor,
                    "raw_name": candidate["raw_name"],
                    "canonical_name": predictor,
                    "unit": candidate["unit"],
                    "relationship_category": category,
                    "mathematical_or_clinical_rationale": rationale,
                    "strict_panel_inclusion": predictor_allowed(category, "strict"),
                    "pragmatic_panel_inclusion": predictor_allowed(category, "pragmatic"),
                    "strict_target_included": target in STRICT_TARGETS,
                    "pragmatic_target_included": target in PRAGMATIC_TARGETS,
                    "source_evidence": source,
                    "review_status": "CONFIRMED_SCHEMA_IDENTITY" if category == "DIRECT_TARGET" else "PROVISIONAL_PENDING_CLINICAL_REVIEW",
                }
            )

    registry = pd.DataFrame(registry_rows)
    evidence = pd.DataFrame([evidence_row(task) for task in tasks])
    write_aggregate_csv(registry, args.output_registry_csv)
    write_aggregate_csv(evidence, args.output_evidence_csv)
    print(
        f"[written] {args.output_registry_csv} ({len(registry)} target/predictor rows; "
        f"{len(tasks)} targets; {len(predictors)} candidate predictors)"
    )
    print(f"[written] {args.output_evidence_csv} ({len(evidence)} target rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
