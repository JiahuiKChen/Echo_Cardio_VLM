#!/usr/bin/env python3
"""Build an SCC-only, non-patient clinical metadata adjudication packet.

The packet contains raw measurement names, descriptions, mappings, and units,
but never measurement values or patient/study/clip/DICOM identifiers.  These
metadata remain SCC-only because they are needed to adjudicate project-specific
aliases and definitions and are not publication-ready registry authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from lvef_multitask_audit_utils import load_table, require_restricted_path, resolve_column, run_guarded


LEGACY29: tuple[str, ...] = (
    "body_surface_area",
    "resting_sbp",
    "resting_dbp",
    "resting_hr",
    "left_ventricular_end_diastolic_diameter",
    "septal_thickness",
    "inf_lat_thickness",
    "la_dimen",
    "sinus_diam",
    "mv_peak_e",
    "mitral_e_velocity",
    "av_pk_vel",
    "la_4ch_length",
    "ra_length",
    "ascending_aorta_diameter",
    "lvot_diam",
    "rv_diam",
    "lvot_vti",
    "mv_peak_a",
    "tr_mmhg",
    "tricuspid_regurgitant_peak_velocity",
    "left_ventricular_end_systolic_diameter",
    "lat_e_prime",
    "sept_e_prime",
    "arch_diam",
    "fs",
    "ivc_diam",
    "height_cm",
    "tricuspid_annular_plane_systolic_excursion",
)
ALLOWED_TARGETS: tuple[str, ...] = ("lvef",) + LEGACY29
ALLOWED_TARGET_SET = frozenset(ALLOWED_TARGETS)

FORBIDDEN_INPUT_COLUMNS = frozenset(
    {
        "subject_id",
        "subject",
        "patient_id",
        "person_id",
        "study_id",
        "study",
        "dicom_study_id",
        "identifier",
        "hadm_id",
        "stay_id",
        "mrn",
        "result",
        "result_numeric",
        "result_value",
        "measurement_value",
        "value",
        "label",
        "prediction",
        "y_true",
        "y_pred",
        "embedding",
        "embedding_idx",
        "clip_key",
        "dicom_path",
        "dicom_filepath",
        "npz_path",
        "path",
        "file_path",
        "absolute_path",
    }
)

# Candidate concepts come from the clinical review questions.  A match only
# selects a source row for review; it never creates or approves a canonical
# mapping.
CANDIDATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "LVEF_ALIAS_OR_METHOD",
        re.compile(
            r"\b(lvef|ef|ejection fraction|left ventricular ef|ef visual|visual ef|"
            r"simpson|biplane|teichholz|three dimensional ef|3d ef)\b",
            re.I,
        ),
    ),
    (
        "LV_VOLUME_OR_SYSTOLIC_NEAR_TARGET",
        re.compile(
            r"\b(lvedv|lvesv|end[ -]?diastolic volume|end[ -]?systolic volume|"
            r"lv systolic function|wall motion|stroke volume|cardiac output|cardiac index)\b",
            re.I,
        ),
    ),
    (
        "TR_PRESSURE_OR_RAP_DERIVATIVE",
        re.compile(
            r"\b(tr gradient|tr peak gradient|rvsp|pasp|pulmonary artery systolic|"
            r"right atrial pressure|rap|ivc collapse|respirophasic)\b",
            re.I,
        ),
    ),
    (
        "MITRAL_RATIO_DERIVATIVE",
        re.compile(r"\b(e[ /]?a ratio|e[ /]?e[' ]?(ratio|prime)?|mitral e a)\b", re.I),
    ),
    (
        "LVOT_AORTIC_DERIVATIVE",
        re.compile(
            r"\b(lvot area|aortic valve area|av area|dimensionless index|doppler velocity index|"
            r"dvi|aortic vti|av vti|stroke volume|cardiac output|cardiac index)\b",
            re.I,
        ),
    ),
    (
        "LV_MASS_OR_INDEXING_DERIVATIVE",
        re.compile(
            r"\b(lv mass|left ventricular mass|relative wall thickness|lavi|"
            r"left atrial volume index|aortic size index|indexed|index)\b",
            re.I,
        ),
    ),
    (
        "ANTHROPOMETRIC_FORMULA_INPUT",
        re.compile(r"\b(body surface area|bsa|height|weight|body weight)\b", re.I),
    ),
)

INDEXED_PATTERN = re.compile(r"\b(index|indexed|indexing|bsa|body surface area|per m2|m\^?2)\b", re.I)
METHOD_PATTERN = re.compile(
    r"\b(visual|simpson|biplane|teichholz|3d|three dimensional|m[ -]?mode|"
    r"doppler|continuous wave|cw|pulsed wave|pw|tissue doppler|tdi|planimetry)\b",
    re.I,
)
VIEW_PATTERN = re.compile(
    r"\b(apical|a4c|a2c|four chamber|two chamber|five chamber|parasternal|plax|psax|"
    r"subcostal|subxiphoid|suprasternal|rv focused|la focused)\b",
    re.I,
)
TIMING_PATTERN = re.compile(
    r"\b(end[ -]?diastol|end[ -]?systol|diastol|systol|end[ -]?expir|inspir|"
    r"sniff|collapse|respirophasic|respiratory|beat|cycle|atrial fibrillation|af)\b",
    re.I,
)

OUTPUT_COLUMNS: tuple[str, ...] = (
    "selection_reason",
    "allowlisted_target",
    "raw_name",
    "raw_description",
    "canonical_mapping",
    "canonical_mapping_status",
    "canonical_source",
    "native_unit",
    "normalized_unit",
    "unit_category",
    "n_raw_aliases_for_canonical",
    "description_overlap_status",
    "multiple_nonunknown_normalized_units",
    "appears_indexed",
    "method_encoded",
    "view_encoded",
    "timing_or_respiratory_context_encoded",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exact_target(value: Any) -> str:
    """Return an allowlisted identifier without repairing or normalizing it."""
    if not isinstance(value, str) or value not in ALLOWED_TARGET_SET:
        raise ValueError("Target is not an exact repository canonical identifier")
    return value


def requested_targets(values: Iterable[str]) -> tuple[str, ...]:
    supplied = list(values)
    if not supplied:
        return ALLOWED_TARGETS
    validated = tuple(exact_target(value) for value in supplied)
    if len(validated) != len(set(validated)):
        raise ValueError("Duplicate requested target")
    return validated


def normalized_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def bool_text_search(pattern: re.Pattern[str], *values: Any) -> bool:
    return bool(pattern.search(" ".join(normalized_text(value) for value in values)))


def candidate_reasons(*values: Any) -> list[str]:
    text = " ".join(normalized_text(value) for value in values)
    return [label for label, pattern in CANDIDATE_PATTERNS if pattern.search(text)]


def assert_metadata_only(frame: pd.DataFrame) -> None:
    forbidden = FORBIDDEN_INPUT_COLUMNS & {str(column).strip().lower() for column in frame.columns}
    if forbidden:
        raise ValueError("Input contains patient-, value-, prediction-, or embedding-level columns")


def resolve_metadata_columns(frame: pd.DataFrame) -> dict[str, str | None]:
    return {
        "raw_name": resolve_column(frame, ("measurement", "raw_name", "raw_measurement"), required=True),
        "raw_description": resolve_column(
            frame,
            ("measurement_description", "description", "raw_description"),
        ),
        "canonical_mapping": resolve_column(
            frame,
            ("canonical_measurement", "canonical_name", "task"),
            required=True,
        ),
        "canonical_source": resolve_column(
            frame,
            ("canonical_source", "mapping_source", "source"),
        ),
        "native_unit": resolve_column(frame, ("unit", "native_unit", "raw_unit")),
        "normalized_unit": resolve_column(frame, ("unit_norm", "normalized_unit", "unit_normalized")),
        "unit_category": resolve_column(frame, ("unit_category", "category")),
    }


def description_status(group: pd.DataFrame) -> str:
    values = [normalized_text(value) for value in group["raw_description"].tolist()]
    present = [value for value in values if value]
    if not present:
        return "NO_DESCRIPTION"
    unique = set(present)
    if len(group) == 1:
        return "SINGLE_RAW_NAME"
    if len(unique) == 1 and len(present) == len(values):
        return "ALIASES_SAME_NORMALIZED_DESCRIPTION"
    if len(unique) == 1:
        return "ALIASES_SAME_DESCRIPTION_WITH_MISSING"
    return "ALIASES_DISTINCT_DESCRIPTIONS"


def build_review_rows(frame: pd.DataFrame, targets: tuple[str, ...]) -> pd.DataFrame:
    assert_metadata_only(frame)
    columns = resolve_metadata_columns(frame)
    raw_name_col = columns["raw_name"]
    canonical_col = columns["canonical_mapping"]
    assert raw_name_col is not None and canonical_col is not None

    working = pd.DataFrame(
        {
            "raw_name": frame[raw_name_col].fillna("").astype(str).str.strip(),
            "raw_description": (
                frame[columns["raw_description"]].fillna("").astype(str).str.strip()
                if columns["raw_description"]
                else ""
            ),
            "canonical_mapping": frame[canonical_col].fillna("").astype(str).str.strip(),
            "canonical_source": (
                frame[columns["canonical_source"]].fillna("").astype(str).str.strip()
                if columns["canonical_source"]
                else "UNKNOWN"
            ),
            "native_unit": (
                frame[columns["native_unit"]].fillna("").astype(str).str.strip()
                if columns["native_unit"]
                else "UNKNOWN"
            ),
            "normalized_unit": (
                frame[columns["normalized_unit"]].fillna("").astype(str).str.strip()
                if columns["normalized_unit"]
                else "UNKNOWN"
            ),
            "unit_category": (
                frame[columns["unit_category"]].fillna("").astype(str).str.strip()
                if columns["unit_category"]
                else "UNKNOWN"
            ),
        }
    )
    if (working["raw_name"] == "").any() or (working["canonical_mapping"] == "").any():
        raise ValueError("Raw name and canonical mapping must be nonblank")

    target_set = set(targets)
    selected_rows: list[dict[str, Any]] = []
    for _, row in working.iterrows():
        canonical = str(row["canonical_mapping"])
        reasons: list[str] = []
        if canonical in target_set:
            reasons.append("ALLOWLISTED_TARGET_MAPPING")
        reasons.extend(
            candidate_reasons(
                row["raw_name"],
                row["raw_description"],
                row["canonical_mapping"],
            )
        )
        if not reasons:
            continue
        selected_rows.append({**row.to_dict(), "selection_reason": ";".join(sorted(set(reasons)))})

    selected = pd.DataFrame(selected_rows)
    if selected.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    alias_count = working.groupby("canonical_mapping", dropna=False)["raw_name"].nunique().to_dict()
    description_map = {
        canonical: description_status(group)
        for canonical, group in working.groupby("canonical_mapping", dropna=False)
    }
    unit_map: dict[str, bool] = {}
    for canonical, group in working.groupby("canonical_mapping", dropna=False):
        units = {
            normalized_text(unit)
            for unit in group["normalized_unit"].tolist()
            if normalized_text(unit) not in {"", "unknown", "na", "nan"}
        }
        unit_map[str(canonical)] = len(units) > 1

    selected["allowlisted_target"] = selected["canonical_mapping"].where(
        selected["canonical_mapping"].isin(target_set), ""
    )
    selected["canonical_mapping_status"] = selected["canonical_mapping"].map(
        lambda value: "EXACT_ALLOWLIST_MATCH" if value in target_set else "SOURCE_CANDIDATE_NOT_ALLOWLISTED"
    )
    selected["n_raw_aliases_for_canonical"] = selected["canonical_mapping"].map(alias_count).astype(int)
    selected["description_overlap_status"] = selected["canonical_mapping"].map(description_map)
    selected["multiple_nonunknown_normalized_units"] = selected["canonical_mapping"].map(unit_map).astype(bool)
    selected["appears_indexed"] = selected.apply(
        lambda row: bool_text_search(
            INDEXED_PATTERN, row["raw_name"], row["raw_description"], row["canonical_mapping"], row["normalized_unit"]
        ),
        axis=1,
    )
    selected["method_encoded"] = selected.apply(
        lambda row: bool_text_search(METHOD_PATTERN, row["raw_name"], row["raw_description"]), axis=1
    )
    selected["view_encoded"] = selected.apply(
        lambda row: bool_text_search(VIEW_PATTERN, row["raw_name"], row["raw_description"]), axis=1
    )
    selected["timing_or_respiratory_context_encoded"] = selected.apply(
        lambda row: bool_text_search(TIMING_PATTERN, row["raw_name"], row["raw_description"]), axis=1
    )
    return selected.loc[:, OUTPUT_COLUMNS].sort_values(
        ["allowlisted_target", "canonical_mapping", "raw_name", "native_unit"], kind="stable"
    ).reset_index(drop=True)


def build_canonical_summary(rows: pd.DataFrame, targets: tuple[str, ...]) -> pd.DataFrame:
    columns = [
        "canonical_mapping",
        "canonical_mapping_status",
        "n_packet_rows",
        "n_raw_aliases",
        "n_distinct_descriptions",
        "n_native_units",
        "n_normalized_units",
        "multiple_nonunknown_normalized_units",
        "any_indexed_candidate",
        "any_method_encoded",
        "any_view_encoded",
        "any_timing_or_respiratory_context_encoded",
    ]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    summary_rows: list[dict[str, Any]] = []
    target_set = set(targets)
    for canonical, group in rows.groupby("canonical_mapping", sort=True):
        normal_units = {
            normalized_text(value)
            for value in group["normalized_unit"]
            if normalized_text(value) not in {"", "unknown", "na", "nan"}
        }
        native_units = {
            normalized_text(value)
            for value in group["native_unit"]
            if normalized_text(value) not in {"", "unknown", "na", "nan"}
        }
        descriptions = {normalized_text(value) for value in group["raw_description"] if normalized_text(value)}
        summary_rows.append(
            {
                "canonical_mapping": canonical,
                "canonical_mapping_status": (
                    "EXACT_ALLOWLIST_MATCH" if canonical in target_set else "SOURCE_CANDIDATE_NOT_ALLOWLISTED"
                ),
                "n_packet_rows": int(len(group)),
                "n_raw_aliases": int(group["raw_name"].nunique()),
                "n_distinct_descriptions": int(len(descriptions)),
                "n_native_units": int(len(native_units)),
                "n_normalized_units": int(len(normal_units)),
                "multiple_nonunknown_normalized_units": len(normal_units) > 1,
                "any_indexed_candidate": bool(group["appears_indexed"].any()),
                "any_method_encoded": bool(group["method_encoded"].any()),
                "any_view_encoded": bool(group["view_encoded"].any()),
                "any_timing_or_respiratory_context_encoded": bool(
                    group["timing_or_respiratory_context_encoded"].any()
                ),
            }
        )
    return pd.DataFrame(summary_rows, columns=columns)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping-csv", type=Path, required=True)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = requested_targets(args.target)
    if not args.mapping_csv.is_file():
        raise FileNotFoundError("Mapping input is missing")
    output_dir = require_restricted_path(args.output_dir)
    if any(output_dir.iterdir()):
        raise ValueError("Clinical metadata output directory must be empty")

    source = load_table(args.mapping_csv)
    rows = build_review_rows(source, targets)
    summary = build_canonical_summary(rows, targets)
    if not set(targets).issubset(set(rows["allowlisted_target"].dropna())):
        missing = set(targets) - set(rows["allowlisted_target"].dropna())
        raise ValueError(f"Requested target mappings are missing ({len(missing)})")

    row_path = output_dir / "clinical_metadata_review_rows.csv"
    summary_path = output_dir / "clinical_metadata_canonical_summary.csv"
    rows.to_csv(row_path, index=False)
    summary.to_csv(summary_path, index=False)

    manifest = {
        "audit": "clinical_metadata_review_packet",
        "status": "COMPLETE",
        "source": {
            "alias": "raw_to_canonical_mapping",
            "bytes": int(args.mapping_csv.stat().st_size),
            "sha256": sha256_file(args.mapping_csv),
        },
        "allowlist_version": "lvef-plus-legacy29-v1",
        "n_allowlisted_targets_requested": len(targets),
        "n_packet_rows": int(len(rows)),
        "n_allowlisted_targets_present": int(rows["allowlisted_target"].replace("", pd.NA).nunique()),
        "n_candidate_canonical_mappings_outside_allowlist": int(
            rows.loc[rows["canonical_mapping_status"] == "SOURCE_CANDIDATE_NOT_ALLOWLISTED", "canonical_mapping"].nunique()
        ),
        "output_files": [
            {
                "relative_path": row_path.name,
                "bytes": int(row_path.stat().st_size),
                "sha256": sha256_file(row_path),
            },
            {
                "relative_path": summary_path.name,
                "bytes": int(summary_path.stat().st_size),
                "sha256": sha256_file(summary_path),
            },
        ],
        "contains_patient_values": False,
        "contains_patient_or_study_identifiers": False,
        "contains_paths_or_locators": False,
        "canonical_candidates_outside_allowlist_are_authority": False,
        "model_fitting_performed": False,
        "test_performance_accessed": False,
    }
    manifest_path = output_dir / "clinical_metadata_review_packet_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    # stdout is aggregate-only and never prints raw names, descriptions, paths,
    # or candidate canonical strings.
    stdout = {key: value for key, value in manifest.items() if key not in {"source", "output_files"}}
    print(json.dumps(stdout, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
