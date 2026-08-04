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
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from lvef_multitask_audit_utils import (
    assert_aggregate_safe_columns,
    assert_aggregate_safe_json,
    load_table,
    require_restricted_path,
    resolve_column,
    run_guarded,
)


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
            r"simpson|biplane|teichholz|three dimensional ef|3d ef|3 d ef)\b",
            re.I,
        ),
    ),
    (
        "LVEF_METHOD_FIELD",
        re.compile(r"\b(visual|simpson|biplane|teichholz|three dimensional|3d|3 d|contrast)\b", re.I),
    ),
    (
        "LVEDV_LVESV_FIELD",
        re.compile(r"\b(lvedv|lvesv|lv edv|lv esv|end[ -]?diastolic volume|end[ -]?systolic volume)\b", re.I),
    ),
    (
        "QUALITATIVE_LV_FUNCTION_FIELD",
        re.compile(
            r"\b(lv systolic function|left ventricular systolic function|global systolic function|"
            r"qualitative lv function|lv function|lv function grade|normal lv function|"
            r"reduced lv function|systolic dysfunction|ejection fraction category)\b",
            re.I,
        ),
    ),
    (
        "WALL_MOTION_FIELD",
        re.compile(r"\b(wall motion|regional wall motion|rwma|hypokinesis|akinesis|dyskinesis)\b", re.I),
    ),
    (
        "STROKE_VOLUME_CARDIAC_OUTPUT_FIELD",
        re.compile(
            r"\b(stroke volume|cardiac output|cardiac index|stroke volume index|sv|co|ci)\b",
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
        re.compile(
            r"\b(e\s*/\s*a|e\s+a\s+ratio|mitral\s+e\s+a|e\s*/\s*e\s+prime|"
            r"e\s+e\s+prime|e\s+over\s+e\s+prime)\b",
            re.I,
        ),
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
        "LV_MASS_OR_RWT_FIELD",
        re.compile(
            r"\b(lv mass|left ventricular mass|relative wall thickness|rwt|lvmi|lv mass index)\b",
            re.I,
        ),
    ),
    (
        "INDEXED_FIELD",
        re.compile(
            r"\b(lavi|left atrial volume index|aortic size index|indexed|index|indexing|per m2)\b|/\s*m2\b",
            re.I,
        ),
    ),
    (
        "ANTHROPOMETRIC_FORMULA_INPUT",
        re.compile(r"\b(body surface area|bsa|height|weight|body weight)\b", re.I),
    ),
)

INDEXED_PATTERN = re.compile(r"\b(index|indexed|indexing|per m2)\b|/\s*m2\b", re.I)
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
    "source_metadata_duplicate_count",
    "raw_alias_maps_to_multiple_canonicals",
    "description_overlap_status",
    "description_is_empty",
    "native_unit_is_unknown",
    "normalized_unit_is_unknown",
    "multiple_nonunknown_normalized_units",
    "appears_indexed",
    "method_encoded",
    "view_encoded",
    "timing_or_respiratory_context_encoded",
    "candidate_lvef_alias_or_method",
    "candidate_lvef_method",
    "candidate_lvedv_lvesv",
    "candidate_qualitative_lv_function",
    "candidate_wall_motion",
    "candidate_formula_derived",
    "candidate_ratio",
    "candidate_stroke_volume_or_cardiac_output",
    "candidate_lv_mass_or_rwt",
    "candidate_indexed",
    "candidate_bsa_or_weight",
)

EVIDENCE_TYPES = frozenset(
    {
        "RESOLVED_BY_PROJECT_METADATA",
        "LITERATURE_ANSWERABLE",
        "REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION",
        "REQUIRES_TECHNICAL_PIPELINE_REVIEW",
        "REQUIRES_VALUE_DISTRIBUTION_AUDIT",
        "NOT_RESOLVABLE_FROM_AVAILABLE_DATA",
    }
)

UNKNOWN_TOKENS = frozenset({"", "unknown", "unk", "na", "n a", "n/a", "nan", "none", "null", "unspecified"})

DIMENSION_TARGETS = frozenset(
    {
        "left_ventricular_end_diastolic_diameter",
        "septal_thickness",
        "inf_lat_thickness",
        "la_dimen",
        "sinus_diam",
        "la_4ch_length",
        "ra_length",
        "ascending_aorta_diameter",
        "lvot_diam",
        "rv_diam",
        "lvot_vti",
        "left_ventricular_end_systolic_diameter",
        "arch_diam",
        "ivc_diam",
        "height_cm",
        "tricuspid_annular_plane_systolic_excursion",
    }
)
VELOCITY_TARGETS = frozenset(
    {
        "mv_peak_e",
        "mitral_e_velocity",
        "av_pk_vel",
        "mv_peak_a",
        "tricuspid_regurgitant_peak_velocity",
        "lat_e_prime",
        "sept_e_prime",
    }
)

REQUIRED_ISSUE_IDS = frozenset(
    {
        "TR_MMHG_DEFINITION",
        "MITRAL_E_FIELD_RELATIONSHIP",
        "INF_LAT_THICKNESS_DEFINITION",
        "LA_DIMEN_PLANE",
        "ARCH_DIAM_LEVEL",
        "SINUS_DIAM_CONVENTION",
        "ASCENDING_AORTA_CONVENTION",
        "IVC_DIAM_CONTEXT",
        "LVEF_METHOD_MIXTURE",
        "LVEF_ALIASES",
        "LVEDV_LVESV_FIELDS",
        "QUALITATIVE_LV_FUNCTION_FIELDS",
        "WALL_MOTION_FIELDS",
        "MITRAL_EA_EEPRIME_RATIO_FIELDS",
        "STROKE_VOLUME_CARDIAC_OUTPUT_FIELDS",
        "LV_MASS_RWT_FIELDS",
        "INDEXED_MEASUREMENTS",
        "BSA_FORMULA_WEIGHT_AVAILABILITY",
        "DIMENSION_CM_MM_UNITS",
        "VELOCITY_MPS_CMPS_UNITS",
    }
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
    text = unicodedata.normalize("NFKC", str(value))
    text = text.translate(
        str.maketrans(
            {
                "′": " prime ",
                "’": " prime ",
                "‘": " prime ",
                "'": " prime ",
                "`": " prime ",
                "″": " double prime ",
                "–": "-",
                "—": "-",
                "−": "-",
                "_": " ",
            }
        )
    ).casefold()
    text = re.sub(r"[^a-z0-9%/+.-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def bool_text_search(pattern: re.Pattern[str], *values: Any) -> bool:
    return bool(pattern.search(" ".join(normalized_text(value) for value in values)))


def candidate_reasons(*values: Any) -> list[str]:
    text = " ".join(normalized_text(value) for value in values)
    return [label for label, pattern in CANDIDATE_PATTERNS if pattern.search(text)]


def unknown_token(value: Any) -> bool:
    return normalized_text(value) in UNKNOWN_TOKENS


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

    working["raw_name_key"] = working["raw_name"].map(normalized_text)
    if (working["raw_name_key"] == "").any():
        raise ValueError("Raw name becomes blank after Unicode/punctuation normalization")
    raw_mapping_count = working.groupby("raw_name_key", dropna=False)["canonical_mapping"].transform("nunique")
    working["raw_alias_maps_to_multiple_canonicals"] = raw_mapping_count > 1
    duplicate_columns = [
        "raw_name",
        "raw_description",
        "canonical_mapping",
        "canonical_source",
        "native_unit",
        "normalized_unit",
        "unit_category",
    ]
    working["source_metadata_duplicate_count"] = working.groupby(
        duplicate_columns, dropna=False
    )["raw_name"].transform("size")

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
            if not unknown_token(unit)
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
    selected["description_is_empty"] = selected["raw_description"].map(lambda value: normalized_text(value) == "")
    selected["native_unit_is_unknown"] = selected["native_unit"].map(unknown_token)
    selected["normalized_unit_is_unknown"] = selected["normalized_unit"].map(unknown_token)
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
    category_sets = selected.apply(
        lambda row: set(
            candidate_reasons(row["raw_name"], row["raw_description"], row["canonical_mapping"])
        ),
        axis=1,
    )
    selected["candidate_lvef_alias_or_method"] = category_sets.map(
        lambda values: "LVEF_ALIAS_OR_METHOD" in values
    )
    selected["candidate_lvef_method"] = category_sets.map(lambda values: "LVEF_METHOD_FIELD" in values)
    selected["candidate_lvedv_lvesv"] = category_sets.map(lambda values: "LVEDV_LVESV_FIELD" in values)
    selected["candidate_qualitative_lv_function"] = category_sets.map(
        lambda values: "QUALITATIVE_LV_FUNCTION_FIELD" in values
    )
    selected["candidate_wall_motion"] = category_sets.map(lambda values: "WALL_MOTION_FIELD" in values)
    selected["candidate_ratio"] = category_sets.map(lambda values: "MITRAL_RATIO_DERIVATIVE" in values)
    selected["candidate_stroke_volume_or_cardiac_output"] = category_sets.map(
        lambda values: "STROKE_VOLUME_CARDIAC_OUTPUT_FIELD" in values
    )
    selected["candidate_lv_mass_or_rwt"] = category_sets.map(
        lambda values: "LV_MASS_OR_RWT_FIELD" in values
    )
    selected["candidate_indexed"] = category_sets.map(lambda values: "INDEXED_FIELD" in values)
    selected["candidate_bsa_or_weight"] = category_sets.map(
        lambda values: "ANTHROPOMETRIC_FORMULA_INPUT" in values
    )
    formula_categories = {
        "LVEDV_LVESV_FIELD",
        "TR_PRESSURE_OR_RAP_DERIVATIVE",
        "MITRAL_RATIO_DERIVATIVE",
        "LVOT_AORTIC_DERIVATIVE",
        "STROKE_VOLUME_CARDIAC_OUTPUT_FIELD",
        "LV_MASS_OR_RWT_FIELD",
        "INDEXED_FIELD",
    }
    selected["candidate_formula_derived"] = category_sets.map(
        lambda values: bool(formula_categories & values)
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
        "n_empty_description_rows",
        "n_exact_duplicate_rows_excess",
        "n_aliases_mapping_to_multiple_canonicals",
        "n_native_units",
        "n_normalized_units",
        "multiple_nonunknown_normalized_units",
        "any_indexed_candidate",
        "any_method_encoded",
        "any_view_encoded",
        "any_timing_or_respiratory_context_encoded",
        "any_lvef_alias_or_method_candidate",
        "any_lvedv_lvesv_candidate",
        "any_formula_derived_candidate",
        "any_ratio_candidate",
        "any_qualitative_lv_function_candidate",
    ]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    summary_rows: list[dict[str, Any]] = []
    target_set = set(targets)
    for canonical, group in rows.groupby("canonical_mapping", sort=True):
        normal_units = {
            normalized_text(value)
            for value in group["normalized_unit"]
            if not unknown_token(value)
        }
        native_units = {
            normalized_text(value)
            for value in group["native_unit"]
            if not unknown_token(value)
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
                "n_empty_description_rows": int(group["description_is_empty"].sum()),
                "n_exact_duplicate_rows_excess": int(
                    len(group)
                    - len(
                        group.drop_duplicates(
                            [
                                "raw_name",
                                "raw_description",
                                "canonical_mapping",
                                "canonical_source",
                                "native_unit",
                                "normalized_unit",
                                "unit_category",
                            ]
                        )
                    )
                ),
                "n_aliases_mapping_to_multiple_canonicals": int(
                    group.loc[group["raw_alias_maps_to_multiple_canonicals"], "raw_name"]
                    .map(normalized_text)
                    .nunique()
                ),
                "n_native_units": int(len(native_units)),
                "n_normalized_units": int(len(normal_units)),
                "multiple_nonunknown_normalized_units": len(normal_units) > 1,
                "any_indexed_candidate": bool(group["appears_indexed"].any()),
                "any_method_encoded": bool(group["method_encoded"].any()),
                "any_view_encoded": bool(group["view_encoded"].any()),
                "any_timing_or_respiratory_context_encoded": bool(
                    group["timing_or_respiratory_context_encoded"].any()
                ),
                "any_lvef_alias_or_method_candidate": bool(group["candidate_lvef_alias_or_method"].any()),
                "any_lvedv_lvesv_candidate": bool(group["candidate_lvedv_lvesv"].any()),
                "any_formula_derived_candidate": bool(group["candidate_formula_derived"].any()),
                "any_ratio_candidate": bool(group["candidate_ratio"].any()),
                "any_qualitative_lv_function_candidate": bool(
                    group["candidate_qualitative_lv_function"].any()
                ),
            }
        )
    return pd.DataFrame(summary_rows, columns=columns)


def unit_family(value: Any, category: Any = "") -> str:
    token = normalized_text(value)
    category_token = normalized_text(category)
    if token in UNKNOWN_TOKENS:
        return "UNKNOWN"
    if token in {"mm", "cm", "m", "millimeter", "centimeter", "meter"}:
        return "LENGTH"
    if token in {"m/s", "cm/s", "mm/s", "m/sec", "cm/sec", "mm/sec"}:
        return "VELOCITY"
    if token in {"%", "percent", "percentage", "fraction", "ratio"}:
        return "FRACTION_OR_PERCENT"
    if token in {"mmhg", "mm hg", "torr"}:
        return "PRESSURE"
    if token in {"bpm", "beats/min", "beats per min", "beats per minute"}:
        return "RATE"
    if token in {"m2", "m 2", "m2 body surface area", "square meter", "square meters"}:
        return "AREA"
    category_map = {
        "length": "LENGTH",
        "velocity": "VELOCITY",
        "fraction": "FRACTION_OR_PERCENT",
        "percent": "FRACTION_OR_PERCENT",
        "pressure": "PRESSURE",
        "rate": "RATE",
        "area": "AREA",
    }
    return category_map.get(category_token, "OTHER_OR_UNRESOLVED")


def expected_unit_family(target: str) -> str:
    if target in DIMENSION_TARGETS:
        return "LENGTH"
    if target in VELOCITY_TARGETS:
        return "VELOCITY"
    if target in {"lvef", "fs"}:
        return "FRACTION_OR_PERCENT"
    if target in {"resting_sbp", "resting_dbp", "tr_mmhg"}:
        return "PRESSURE"
    if target == "resting_hr":
        return "RATE"
    if target == "body_surface_area":
        return "AREA"
    return "OTHER_OR_UNRESOLVED"


def build_unit_summary(rows: pd.DataFrame, targets: tuple[str, ...]) -> pd.DataFrame:
    columns = [
        "target",
        "n_source_rows",
        "n_known_unit_rows",
        "n_unknown_unit_rows",
        "n_native_unit_tokens",
        "n_normalized_unit_tokens",
        "n_observed_unit_families",
        "expected_unit_family",
        "unit_resolution_status",
        "unit_family_match_status",
        "any_indexed_candidate",
    ]
    output: list[dict[str, Any]] = []
    for target in targets:
        group = rows[rows["allowlisted_target"] == target].copy()
        known_mask = ~group["normalized_unit_is_unknown"] if not group.empty else pd.Series(dtype=bool)
        known = group[known_mask] if not group.empty else group
        normalized_units = {normalized_text(value) for value in known["normalized_unit"]}
        native_units = {
            normalized_text(value)
            for value in group.loc[~group["native_unit_is_unknown"], "native_unit"]
        }
        families = {
            unit_family(row["normalized_unit"], row["unit_category"])
            for _, row in known.iterrows()
        }
        families.discard("UNKNOWN")
        if group.empty:
            unit_status = "NO_SOURCE_ROWS"
        elif not normalized_units:
            unit_status = "ALL_UNKNOWN"
        elif bool(group["normalized_unit_is_unknown"].any()):
            unit_status = "MIXED_KNOWN_AND_UNKNOWN"
        elif len(normalized_units) == 1:
            unit_status = "SINGLE_KNOWN_UNIT"
        elif len(families) == 1:
            unit_status = "MULTIPLE_SAME_FAMILY_CONVERSION_REQUIRED"
        else:
            unit_status = "MULTIPLE_INCOMPATIBLE_OR_UNRESOLVED"
        expected = expected_unit_family(target)
        if not families:
            family_status = "UNRESOLVED_NO_KNOWN_UNIT"
        elif expected == "OTHER_OR_UNRESOLVED":
            family_status = "EXPECTED_FAMILY_NOT_PRESPECIFIED"
        elif families == {expected}:
            family_status = "MATCH"
        else:
            family_status = "MISMATCH_OR_MIXED"
        output.append(
            {
                "target": target,
                "n_source_rows": int(len(group)),
                "n_known_unit_rows": int(len(known)),
                "n_unknown_unit_rows": int(group["normalized_unit_is_unknown"].sum()) if not group.empty else 0,
                "n_native_unit_tokens": len(native_units),
                "n_normalized_unit_tokens": len(normalized_units),
                "n_observed_unit_families": len(families),
                "expected_unit_family": expected,
                "unit_resolution_status": unit_status,
                "unit_family_match_status": family_status,
                "any_indexed_candidate": bool(group["candidate_indexed"].any()) if not group.empty else False,
            }
        )
    return pd.DataFrame(output, columns=columns)


def build_alias_summary(rows: pd.DataFrame, targets: tuple[str, ...]) -> pd.DataFrame:
    columns = [
        "target",
        "n_source_rows",
        "n_unique_raw_aliases",
        "n_exact_duplicate_rows_excess",
        "n_empty_description_rows",
        "n_distinct_descriptions",
        "n_aliases_mapping_to_multiple_canonicals",
        "description_overlap_status",
        "alias_resolution_status",
    ]
    output: list[dict[str, Any]] = []
    duplicate_columns = [
        "raw_name",
        "raw_description",
        "canonical_mapping",
        "canonical_source",
        "native_unit",
        "normalized_unit",
        "unit_category",
    ]
    for target in targets:
        group = rows[rows["allowlisted_target"] == target].copy()
        descriptions = {normalized_text(value) for value in group["raw_description"] if normalized_text(value)}
        n_aliases = int(group["raw_name"].map(normalized_text).nunique()) if not group.empty else 0
        duplicates = int(len(group) - len(group.drop_duplicates(duplicate_columns))) if not group.empty else 0
        conflicts = int(
            group.loc[group["raw_alias_maps_to_multiple_canonicals"], "raw_name"].map(normalized_text).nunique()
        ) if not group.empty else 0
        empty_descriptions = int(group["description_is_empty"].sum()) if not group.empty else 0
        if group.empty:
            description_overlap = "NO_SOURCE_ROWS"
            alias_status = "NO_SOURCE_ROWS"
        else:
            description_overlap = description_status(group)
            if conflicts:
                alias_status = "RAW_ALIAS_CANONICAL_CONFLICT"
            elif empty_descriptions:
                alias_status = "MISSING_DESCRIPTION"
            elif n_aliases == 1:
                alias_status = "ONE_ALIAS_COMPLETE"
            elif len(descriptions) == 1:
                alias_status = "MULTIPLE_ALIASES_SAME_DESCRIPTION"
            else:
                alias_status = "MULTIPLE_ALIASES_DISTINCT_DESCRIPTIONS"
        output.append(
            {
                "target": target,
                "n_source_rows": int(len(group)),
                "n_unique_raw_aliases": n_aliases,
                "n_exact_duplicate_rows_excess": duplicates,
                "n_empty_description_rows": empty_descriptions,
                "n_distinct_descriptions": len(descriptions),
                "n_aliases_mapping_to_multiple_canonicals": conflicts,
                "description_overlap_status": description_overlap,
                "alias_resolution_status": alias_status,
            }
        )
    return pd.DataFrame(output, columns=columns)


def _group_for_targets(rows: pd.DataFrame, *targets: str) -> pd.DataFrame:
    return rows[rows["allowlisted_target"].isin(targets)].copy()


def _group_for_flag(rows: pd.DataFrame, flag: str) -> pd.DataFrame:
    return rows[rows[flag].astype(bool)].copy()


def _metadata_blob(group: pd.DataFrame, include_names: bool = True) -> str:
    if group.empty:
        return ""
    values: list[Any] = []
    if include_names:
        values.extend(group["raw_name"].tolist())
    values.extend(group["raw_description"].tolist())
    return " ".join(normalized_text(value) for value in values)


def _issue(
    issue_id: str,
    group: pd.DataFrame,
    evidence_type: str,
    reason_code: str,
    targets: tuple[str, ...] = (),
) -> dict[str, Any]:
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(f"Invalid evidence type for {issue_id}")
    affected = set(group["allowlisted_target"].dropna()) - {""} if not group.empty else set()
    affected.update(target for target in targets if target in ALLOWED_TARGET_SET)
    return {
        "issue_id": issue_id,
        "evidence_type": evidence_type,
        "resolution_status": "RESOLVED" if evidence_type == "RESOLVED_BY_PROJECT_METADATA" else "UNRESOLVED",
        "n_restricted_rows": int(len(group)),
        "n_affected_allowlisted_targets": len(affected),
        "clinician_question_required": evidence_type == "REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION",
        "literature_followup_required": evidence_type == "LITERATURE_ANSWERABLE",
        "technical_review_required": evidence_type == "REQUIRES_TECHNICAL_PIPELINE_REVIEW",
        "value_distribution_audit_required": evidence_type == "REQUIRES_VALUE_DISTRIBUTION_AUDIT",
        "reason_code": reason_code,
        "_row_indices": group.index.tolist(),
        "_targets": sorted(affected),
    }


def _definition_issue(
    rows: pd.DataFrame,
    target: str,
    issue_id: str,
    required_pattern_groups: tuple[tuple[str, ...], ...],
) -> dict[str, Any]:
    group = _group_for_targets(rows, target)
    if group.empty:
        return _issue(issue_id, group, "NOT_RESOLVABLE_FROM_AVAILABLE_DATA", "TARGET_ROWS_ABSENT", (target,))
    if bool(group["description_is_empty"].any()):
        return _issue(
            issue_id,
            group,
            "REQUIRES_TECHNICAL_PIPELINE_REVIEW",
            "ONE_OR_MORE_DESCRIPTIONS_EMPTY",
            (target,),
        )
    every_alias_complete = True
    for _, row in group.iterrows():
        blob = " ".join(normalized_text(row[value]) for value in ("raw_name", "raw_description"))
        if not all(any(term in blob for term in pattern_group) for pattern_group in required_pattern_groups):
            every_alias_complete = False
            break
    if every_alias_complete:
        return _issue(issue_id, group, "RESOLVED_BY_PROJECT_METADATA", "EXPLICIT_METADATA_DEFINITION", (target,))
    return _issue(
        issue_id,
        group,
        "REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION",
        "DESCRIPTION_PRESENT_BUT_DEFINITION_INCOMPLETE",
        (target,),
    )


def _method_categories(group: pd.DataFrame) -> set[str]:
    blob = _metadata_blob(group)
    categories: set[str] = set()
    patterns = {
        "VISUAL": ("visual",),
        "SIMPSON_BIPLANE": ("simpson", "biplane"),
        "TEICHHOLZ_LINEAR": ("teichholz", "m-mode", "m mode"),
        "THREE_DIMENSIONAL": ("3d", "3 d", "three dimensional"),
        "CONTRAST": ("contrast",),
    }
    for category, terms in patterns.items():
        if any(term in blob for term in terms):
            categories.add(category)
    return categories


def _mitral_e_semantic_classes(group: pd.DataFrame) -> set[str]:
    """Classify only explicit acquisition/site semantics, never lexical similarity."""
    blob = _metadata_blob(group)
    classes: set[str] = set()
    if any(
        term in blob
        for term in (
            "transmitral",
            "mitral inflow",
            "mv e wave",
            "mitral e wave",
            "peak e velocity",
        )
    ):
        classes.add("TRANSMITRAL_INFLOW_E")
    if any(
        term in blob
        for term in (
            "tissue doppler",
            "tdi",
            "mitral annular",
            "annular e prime",
            "e prime velocity",
        )
    ):
        classes.add("TISSUE_DOPPLER_E_PRIME")
    if any(term in blob for term in ("pulmonary venous", "pulmonary vein")):
        classes.add("PULMONARY_VENOUS_VELOCITY")
    return classes


def classify_unresolved_questions(
    rows: pd.DataFrame,
    unit_summary: pd.DataFrame,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    tr = _group_for_targets(rows, "tr_mmhg")
    tr_row_categories: list[set[str]] = []
    for _, row in tr.iterrows():
        blob = " ".join(normalized_text(row[value]) for value in ("raw_name", "raw_description"))
        categories: set[str] = set()
        if any(term in blob for term in ("peak gradient", "tr gradient", "rv-ra gradient", "rv ra gradient")):
            categories.add("PEAK_GRADIENT")
        if any(term in blob for term in ("rvsp", "pasp", "right ventricular systolic pressure", "pulmonary artery systolic pressure")):
            categories.add("RVSP_OR_PASP")
        tr_row_categories.append(categories)
    tr_categories = set().union(*tr_row_categories) if tr_row_categories else set()
    if not tr.empty and bool(tr["description_is_empty"].any()):
        issues.append(_issue("TR_MMHG_DEFINITION", tr, "REQUIRES_TECHNICAL_PIPELINE_REVIEW", "ONE_OR_MORE_DESCRIPTIONS_EMPTY", ("tr_mmhg",)))
    elif tr_row_categories and len(tr_categories) == 1 and all(categories == tr_categories for categories in tr_row_categories):
        issues.append(_issue("TR_MMHG_DEFINITION", tr, "RESOLVED_BY_PROJECT_METADATA", "ONE_EXPLICIT_PRESSURE_CONSTRUCT", ("tr_mmhg",)))
    elif len(tr_categories) > 1:
        issues.append(_issue("TR_MMHG_DEFINITION", tr, "REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION", "MIXED_PRESSURE_CONSTRUCTS", ("tr_mmhg",)))
    elif not tr.empty:
        issues.append(_issue("TR_MMHG_DEFINITION", tr, "REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION", "PRESSURE_DEFINITION_INCOMPLETE", ("tr_mmhg",)))
    else:
        issues.append(_issue("TR_MMHG_DEFINITION", tr, "REQUIRES_TECHNICAL_PIPELINE_REVIEW", "NO_INTERPRETABLE_PRESSURE_DESCRIPTION", ("tr_mmhg",)))

    mitral = _group_for_targets(rows, "mv_peak_e", "mitral_e_velocity")
    mv = _group_for_targets(rows, "mv_peak_e")
    alt = _group_for_targets(rows, "mitral_e_velocity")
    mv_desc = {normalized_text(value) for value in mv["raw_description"] if normalized_text(value)}
    alt_desc = {normalized_text(value) for value in alt["raw_description"] if normalized_text(value)}
    mv_units = {normalized_text(value) for value in mv["normalized_unit"] if not unknown_token(value)}
    alt_units = {normalized_text(value) for value in alt["normalized_unit"] if not unknown_token(value)}
    mv_unit_families = {unit_family(value) for value in mv_units}
    alt_unit_families = {unit_family(value) for value in alt_units}
    mv_semantics = _mitral_e_semantic_classes(mv)
    alt_semantics = _mitral_e_semantic_classes(alt)
    if mv.empty or alt.empty:
        mitral_type, mitral_reason = "NOT_RESOLVABLE_FROM_AVAILABLE_DATA", "ONE_CANONICAL_FIELD_ABSENT"
    elif not mv_desc or not alt_desc:
        mitral_type, mitral_reason = "REQUIRES_TECHNICAL_PIPELINE_REVIEW", "DESCRIPTION_MISSING"
    elif mv_desc == alt_desc and mv_units == alt_units and mv_units:
        mitral_type, mitral_reason = "REQUIRES_VALUE_DISTRIBUTION_AUDIT", "METADATA_EQUIVALENT_VALUES_NOT_CHECKED"
    elif not mv_units or not alt_units:
        mitral_type, mitral_reason = "REQUIRES_TECHNICAL_PIPELINE_REVIEW", "UNIT_MISSING"
    elif mv_semantics and alt_semantics and mv_semantics.isdisjoint(alt_semantics):
        mitral_type, mitral_reason = "RESOLVED_BY_PROJECT_METADATA", "EXPLICIT_INCOMPATIBLE_ACQUISITION_OR_SITE_SEMANTICS"
    elif (
        mv_semantics
        and mv_semantics == alt_semantics
        and mv_unit_families == alt_unit_families
        and mv_unit_families <= {"VELOCITY"}
    ):
        mitral_type, mitral_reason = "REQUIRES_VALUE_DISTRIBUTION_AUDIT", "SEMANTICALLY_EQUIVALENT_METADATA_VALUES_NOT_CHECKED"
    else:
        mitral_type, mitral_reason = "REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION", "SEMANTIC_RELATIONSHIP_INCOMPLETE_OR_MIXED"
    issues.append(_issue("MITRAL_E_FIELD_RELATIONSHIP", mitral, mitral_type, mitral_reason, ("mv_peak_e", "mitral_e_velocity")))

    definition_specs = (
        (
            "INF_LAT_THICKNESS_DEFINITION",
            "inf_lat_thickness",
            (("posterior", "inferolateral", "inf lat"), ("wall",), ("thickness",), ("end diastolic", "end-diastolic")),
        ),
        (
            "LA_DIMEN_PLANE",
            "la_dimen",
            (
                ("anteroposterior", "ap dimension", "parasternal", "plax"),
                ("end systolic", "end-systolic", "end systole", "end-systole"),
            ),
        ),
        ("ARCH_DIAM_LEVEL", "arch_diam", (("proximal", "transverse", "distal", "isthmus"), ("arch",))),
        (
            "SINUS_DIAM_CONVENTION",
            "sinus_diam",
            (
                ("sinus of valsalva", "aortic sinus"),
                ("leading edge", "leading-edge", "inner edge", "inner-edge"),
                ("end diastolic", "end-diastolic", "end diastole", "end-diastole", "systolic"),
            ),
        ),
        (
            "ASCENDING_AORTA_CONVENTION",
            "ascending_aorta_diameter",
            (
                ("ascending aorta", "tubular ascending"),
                ("leading edge", "leading-edge", "inner edge", "inner-edge"),
                ("end diastolic", "end-diastolic", "end diastole", "end-diastole", "systolic"),
            ),
        ),
        ("IVC_DIAM_CONTEXT", "ivc_diam", (("inspir", "expir", "sniff", "collapse", "respirophasic", "respiratory"),)),
    )
    identity_by_id: dict[str, dict[str, Any]] = {}
    for issue_id, target, patterns in definition_specs:
        result = _definition_issue(rows, target, issue_id, patterns)
        issues.append(result)
        identity_by_id[issue_id] = result

    lvef = _group_for_targets(rows, "lvef")
    methods_by_row = [_method_categories(lvef.loc[[index]]) for index in lvef.index]
    methods = set().union(*methods_by_row) if methods_by_row else set()
    if lvef.empty or bool(lvef["description_is_empty"].any()) or any(not values for values in methods_by_row):
        lvef_method_type, lvef_method_reason = "REQUIRES_TECHNICAL_PIPELINE_REVIEW", "METHOD_NOT_ENCODED"
    elif len(methods) == 1 and all(values == methods for values in methods_by_row):
        lvef_method_type, lvef_method_reason = "RESOLVED_BY_PROJECT_METADATA", "ONE_EXPLICIT_METHOD_CATEGORY"
    else:
        lvef_method_type, lvef_method_reason = "REQUIRES_ECHOCARDIOGRAPHER_ADJUDICATION", "MULTIPLE_OR_MIXED_METHOD_CATEGORIES"
    issues.append(_issue("LVEF_METHOD_MIXTURE", lvef, lvef_method_type, lvef_method_reason, ("lvef",)))

    lvef_alias = _group_for_flag(rows, "candidate_lvef_alias_or_method")
    lvef_alias = lvef_alias[lvef_alias["canonical_mapping"] != "lvef"]
    if lvef_alias.empty:
        issues.append(_issue("LVEF_ALIASES", lvef_alias, "RESOLVED_BY_PROJECT_METADATA", "COMPLETE_SEARCH_NO_CANDIDATE"))
    else:
        issues.append(_issue("LVEF_ALIASES", lvef_alias, "REQUIRES_TECHNICAL_PIPELINE_REVIEW", "CANDIDATE_ALIASES_REQUIRE_MAPPING_REVIEW", ("lvef",)))

    presence_specs = (
        ("LVEDV_LVESV_FIELDS", "candidate_lvedv_lvesv"),
        ("QUALITATIVE_LV_FUNCTION_FIELDS", "candidate_qualitative_lv_function"),
        ("WALL_MOTION_FIELDS", "candidate_wall_motion"),
        ("MITRAL_EA_EEPRIME_RATIO_FIELDS", "candidate_ratio"),
        ("STROKE_VOLUME_CARDIAC_OUTPUT_FIELDS", "candidate_stroke_volume_or_cardiac_output"),
        ("LV_MASS_RWT_FIELDS", "candidate_lv_mass_or_rwt"),
    )
    for issue_id, flag in presence_specs:
        group = _group_for_flag(rows, flag)
        if group.empty:
            evidence_type = "RESOLVED_BY_PROJECT_METADATA"
            reason = "COMPLETE_SEARCH_NO_CANDIDATE"
        else:
            evidence_type = "REQUIRES_TECHNICAL_PIPELINE_REVIEW"
            reason = "PATTERN_CANDIDATE_PRESENT_REQUIRES_EXACT_MAPPING_REVIEW"
        issues.append(_issue(issue_id, group, evidence_type, reason))

    indexed = _group_for_flag(rows, "candidate_indexed")
    if indexed.empty:
        issues.append(_issue("INDEXED_MEASUREMENTS", indexed, "RESOLVED_BY_PROJECT_METADATA", "COMPLETE_SEARCH_NO_INDEXED_CANDIDATE"))
    else:
        issues.append(_issue("INDEXED_MEASUREMENTS", indexed, "REQUIRES_TECHNICAL_PIPELINE_REVIEW", "INDEXED_FIELDS_REQUIRE_NUMERATOR_DENOMINATOR_MAPPING"))

    bsa = _group_for_flag(rows, "candidate_bsa_or_weight")
    bsa_blob = _metadata_blob(bsa)
    formula_named = any(value in bsa_blob for value in ("mosteller", "du bois", "dubois", "haycock"))
    weight_present = bool(re.search(r"\b(weight|body weight)\b", bsa_blob))
    if formula_named and weight_present:
        bsa_type, bsa_reason = "RESOLVED_BY_PROJECT_METADATA", "FORMULA_AND_WEIGHT_EXPLICIT"
    else:
        bsa_type, bsa_reason = "REQUIRES_TECHNICAL_PIPELINE_REVIEW", "FORMULA_OR_WEIGHT_NOT_EXPLICIT"
    issues.append(_issue("BSA_FORMULA_WEIGHT_AVAILABILITY", bsa, bsa_type, bsa_reason, ("body_surface_area", "height_cm")))

    for issue_id, target_set in (
        ("DIMENSION_CM_MM_UNITS", DIMENSION_TARGETS),
        ("VELOCITY_MPS_CMPS_UNITS", VELOCITY_TARGETS),
    ):
        summary = unit_summary[unit_summary["target"].isin(target_set)]
        unresolved = summary[
            (summary["unit_resolution_status"] != "SINGLE_KNOWN_UNIT")
            | (summary["unit_family_match_status"] != "MATCH")
        ]
        group = rows[rows["allowlisted_target"].isin(unresolved["target"])].copy()
        if unresolved.empty:
            issues.append(_issue(issue_id, group, "RESOLVED_BY_PROJECT_METADATA", "ALL_TARGET_UNITS_SINGLE_AND_MATCHED", tuple(target_set)))
        else:
            issues.append(_issue(issue_id, group, "REQUIRES_TECHNICAL_PIPELINE_REVIEW", "UNKNOWN_MULTIPLE_OR_MISMATCHED_UNITS", tuple(unresolved["target"])))

    literature_map = (
        ("INF_LAT_THICKNESS_DEFINITION", "INF_LAT_THICKNESS_MEASUREMENT_EVIDENCE", "inf_lat_thickness"),
        ("LA_DIMEN_PLANE", "LA_DIMEN_MEASUREMENT_EVIDENCE", "la_dimen"),
        ("ARCH_DIAM_LEVEL", "ARCH_DIAM_MEASUREMENT_EVIDENCE", "arch_diam"),
        ("SINUS_DIAM_CONVENTION", "SINUS_DIAM_MEASUREMENT_EVIDENCE", "sinus_diam"),
        ("ASCENDING_AORTA_CONVENTION", "ASCENDING_AORTA_MEASUREMENT_EVIDENCE", "ascending_aorta_diameter"),
        ("IVC_DIAM_CONTEXT", "IVC_DIAM_MEASUREMENT_EVIDENCE", "ivc_diam"),
    )
    for identity_id, evidence_id, target in literature_map:
        target_units = unit_summary[unit_summary["target"] == target]
        units_ready = bool(
            len(target_units) == 1
            and target_units.iloc[0]["unit_resolution_status"] == "SINGLE_KNOWN_UNIT"
            and target_units.iloc[0]["unit_family_match_status"] == "MATCH"
        )
        if identity_by_id[identity_id]["evidence_type"] == "RESOLVED_BY_PROJECT_METADATA" and units_ready:
            group = _group_for_targets(rows, target)
            issues.append(_issue(evidence_id, group, "LITERATURE_ANSWERABLE", "IDENTITY_RESOLVED_MEASUREMENT_EVIDENCE_GAP", (target,)))

    return issues


QUESTION_BANK: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "TR_MMHG_DEFINITION": (
        "Meaning of tr_mmhg",
        "Which pressure construct is explicitly represented by the restricted raw metadata?",
        ("Peak RV-RA/TR gradient", "RVSP or PASP including RAP", "Mixed constructs", "Another construct", "Unresolved"),
    ),
    "MITRAL_E_FIELD_RELATIONSHIP": (
        "Relationship between mitral E fields",
        "Do the descriptions define the same acquisition construct after unit reconciliation?",
        ("Same construct", "Same construct after unit conversion", "Distinct constructs", "Mixed/overlapping", "Unresolved"),
    ),
    "INF_LAT_THICKNESS_DEFINITION": (
        "Meaning of inf_lat_thickness",
        "Which wall, phase, and acquisition convention does this field represent?",
        ("Standard end-diastolic posterior wall", "Inferolateral 2-D wall", "Mixed", "Other", "Unresolved"),
    ),
    "LA_DIMEN_PLANE": (
        "LA dimension plane",
        "Which anatomic plane and timing define la_dimen?",
        ("PLAX anteroposterior at LV end-systole", "Another named plane", "Mixed planes", "Unresolved"),
    ),
    "ARCH_DIAM_LEVEL": (
        "Aortic arch level",
        "Which named arch level and edge convention define arch_diam?",
        ("Proximal", "Transverse", "Distal/isthmus", "Mixed", "Unresolved"),
    ),
    "SINUS_DIAM_CONVENTION": (
        "Aortic sinus convention",
        "Which edge and timing convention define sinus_diam?",
        ("Leading-edge end-diastolic", "Inner-edge end-diastolic", "Another explicit convention", "Mixed", "Unresolved"),
    ),
    "ASCENDING_AORTA_CONVENTION": (
        "Ascending-aorta convention",
        "Which segment, edge, and timing convention define ascending_aorta_diameter?",
        ("Leading-edge end-diastolic", "Inner-edge end-diastolic", "Another explicit convention", "Mixed", "Unresolved"),
    ),
    "IVC_DIAM_CONTEXT": (
        "IVC diameter context",
        "Which respiratory phase, ventilation context, and collapse information define ivc_diam?",
        ("End-expiratory with collapse data", "Diameter without collapse", "Mixed phase/ventilation", "Other", "Unresolved"),
    ),
    "LVEF_METHOD_MIXTURE": (
        "LVEF method mixture",
        "Are the explicitly described LVEF methods clinically harmonizable for this label-completion estimand?",
        ("One method", "Multiple methods but harmonizable", "Requires method stratification", "Unsafe mixture", "Unresolved"),
    ),
}


def _markdown_escape(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def build_clinician_questionnaire(issues: list[dict[str, Any]], rows: pd.DataFrame) -> str:
    questions = [issue for issue in issues if issue["clinician_question_required"]]
    lines = [
        "# Restricted clinician questionnaire",
        "",
        "Generated mechanically from the clinical metadata packet. It contains only issues not resolved by exact project metadata and therefore remains restricted. No patient values, identifiers, model outputs, or locators are included.",
        "",
    ]
    if not questions:
        lines.extend(["No echocardiographer questions remain after mechanical metadata review.", ""])
        return "\n".join(lines)
    for number, issue in enumerate(questions, start=1):
        title, prompt, choices = QUESTION_BANK.get(
            issue["issue_id"],
            (issue["issue_id"], "Select the best supported interpretation.", ("Resolved", "Unresolved")),
        )
        lines.extend([f"## Q{number}: {_markdown_escape(title)}", "", _markdown_escape(prompt), ""])
        group = rows.loc[issue["_row_indices"]] if issue["_row_indices"] else rows.iloc[0:0]
        if not group.empty:
            lines.extend(
                [
                    "| Exact target | Raw name | Raw description | Native unit | Normalized unit | Mapping source |",
                    "|---|---|---|---|---|---|",
                ]
            )
            for _, row in group.iterrows():
                lines.append(
                    "| "
                    + " | ".join(
                        _markdown_escape(row[column])
                        for column in (
                            "allowlisted_target",
                            "raw_name",
                            "raw_description",
                            "native_unit",
                            "normalized_unit",
                            "canonical_source",
                        )
                    )
                    + " |"
                )
            lines.append("")
        lines.extend([f"- [ ] {chr(65 + index)}. {_markdown_escape(choice)}" for index, choice in enumerate(choices)])
        lines.extend(["", "Clinician initials: ______    Date: ______", ""])
    return "\n".join(lines)


def build_restricted_issue_details(issues: list[dict[str, Any]], rows: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "issue_id",
        "evidence_type",
        "reason_code",
        "allowlisted_target",
        "raw_name",
        "raw_description",
        "canonical_mapping",
        "canonical_source",
        "native_unit",
        "normalized_unit",
        "unit_category",
    ]
    output: list[dict[str, Any]] = []
    for issue in issues:
        group = rows.loc[issue["_row_indices"]] if issue["_row_indices"] else rows.iloc[0:0]
        if group.empty:
            output.append(
                {
                    "issue_id": issue["issue_id"],
                    "evidence_type": issue["evidence_type"],
                    "reason_code": issue["reason_code"],
                    **{column: "" for column in columns[3:]},
                }
            )
            continue
        for _, row in group.iterrows():
            output.append(
                {
                    "issue_id": issue["issue_id"],
                    "evidence_type": issue["evidence_type"],
                    "reason_code": issue["reason_code"],
                    **{column: row[column] for column in columns[3:]},
                }
            )
    return pd.DataFrame(output, columns=columns)


def build_literature_candidates(issues: list[dict[str, Any]], rows: pd.DataFrame) -> pd.DataFrame:
    selected = [issue for issue in issues if issue["literature_followup_required"]]
    details = build_restricted_issue_details(selected, rows) if selected else pd.DataFrame()
    columns = [
        "issue_id",
        "evidence_type",
        "allowlisted_target",
        "raw_name",
        "raw_description",
        "native_unit",
        "normalized_unit",
        "claim_level_question_pending_safe_review",
    ]
    if details.empty:
        return pd.DataFrame(columns=columns)
    output = details[
        ["issue_id", "evidence_type", "allowlisted_target", "raw_name", "raw_description", "native_unit", "normalized_unit"]
    ].copy()
    output["claim_level_question_pending_safe_review"] = True
    return output.loc[:, columns]


LITERATURE_QUESTION_BANK: dict[str, str] = {
    "INF_LAT_THICKNESS_MEASUREMENT_EVIDENCE": (
        "What professional measurement-methodology evidence defines this exact wall-thickness construct, "
        "including phase and acquisition convention, and what construct-specific reader reproducibility is reported?"
    ),
    "LA_DIMEN_MEASUREMENT_EVIDENCE": (
        "What professional measurement-methodology evidence defines this exact LA linear dimension, plane, and timing, "
        "and what construct-specific reader reproducibility is reported?"
    ),
    "ARCH_DIAM_MEASUREMENT_EVIDENCE": (
        "What professional measurement-methodology evidence defines measurement at this exact aortic-arch level, "
        "including edge and timing conventions, and what construct-specific reader reproducibility is reported?"
    ),
    "SINUS_DIAM_MEASUREMENT_EVIDENCE": (
        "What professional measurement-methodology evidence defines this exact sinus-of-Valsalva measurement, "
        "including edge and timing conventions, and what construct-specific reader reproducibility is reported?"
    ),
    "ASCENDING_AORTA_MEASUREMENT_EVIDENCE": (
        "What professional measurement-methodology evidence defines this exact ascending-aortic measurement, "
        "including segment, edge, and timing conventions, and what construct-specific reader reproducibility is reported?"
    ),
    "IVC_DIAM_MEASUREMENT_EVIDENCE": (
        "What professional measurement-methodology evidence defines this exact IVC-diameter construct, including "
        "respiratory phase and ventilation context, and what construct-specific reader reproducibility is reported?"
    ),
}

FOLLOWUP_UNSAFE_PATTERN = re.compile(
    r"(?i)(subject[_ -]?id|study[_ -]?id|patient[_ -]?id|person[_ -]?id|mrn|"
    r"accession|hadm[_ -]?id|stay[_ -]?id|clip[_ -]?key|dicom|"
    r"/restricted/|s3://|file://|\.dcm\b|\.npz\b|\b\d{7,}\b)"
)


def _followup_text_is_safe(value: Any) -> bool:
    text = str(value)
    if not text.strip() or len(text) > 500:
        return False
    if any(character in text for character in ("\r", "\n", "\x00")):
        return False
    return not bool(FOLLOWUP_UNSAFE_PATTERN.search(text))


def build_targeted_followup_prompt(
    literature_candidates: pd.DataFrame,
) -> tuple[str, dict[str, Any]]:
    """Build a separately gated prompt; raw names and mapping sources are never emitted."""
    required_columns = {
        "issue_id",
        "evidence_type",
        "allowlisted_target",
        "raw_description",
        "native_unit",
        "normalized_unit",
    }
    if not required_columns.issubset(literature_candidates.columns):
        raise ValueError("Literature candidate schema is incomplete")

    selected = literature_candidates[
        literature_candidates["evidence_type"] == "LITERATURE_ANSWERABLE"
    ].copy()
    if selected.empty:
        markdown = (
            "# Targeted OpenEvidence follow-up\n\n"
            "Status: `NOT_GENERATED_ZERO_LITERATURE_ANSWERABLE_AMBIGUITIES`.\n\n"
            "No copy-ready prompt was generated because the mechanically classified restricted metadata packet "
            "contained no literature-answerable ambiguity. Dataset-identity, alias, unit, technical-pipeline, "
            "value-distribution, and clinician-adjudication questions must not be redirected to OpenEvidence.\n"
        )
        gate = {
            "audit": "openevidence_targeted_followup_safety_gate",
            "status": "PASS_NO_PROMPT_REQUIRED",
            "safety_gate_passed": True,
            "prompt_generated": False,
            "reason": "ZERO_LITERATURE_ANSWERABLE_AMBIGUITIES",
            "n_literature_answerable_issues": 0,
            "n_allowlisted_targets": 0,
            "raw_names_emitted": False,
            "mapping_sources_emitted": False,
            "patient_values_or_identifiers_emitted": False,
            "paths_or_locators_emitted": False,
        }
        return markdown, gate

    target_values = selected["allowlisted_target"].dropna().astype(str).tolist()
    issue_values = selected["issue_id"].dropna().astype(str).tolist()
    unsafe_reasons: list[str] = []
    if any(target not in ALLOWED_TARGET_SET for target in target_values):
        unsafe_reasons.append("NON_ALLOWLISTED_TARGET")
    if any(issue not in LITERATURE_QUESTION_BANK for issue in issue_values):
        unsafe_reasons.append("UNAPPROVED_LITERATURE_QUESTION")
    for column in ("raw_description", "native_unit", "normalized_unit"):
        if any(not _followup_text_is_safe(value) for value in selected[column].tolist()):
            unsafe_reasons.append(f"UNSAFE_OR_EMPTY_{column.upper()}")
    if unsafe_reasons:
        gate = {
            "audit": "openevidence_targeted_followup_safety_gate",
            "status": "FAIL",
            "safety_gate_passed": False,
            "prompt_generated": False,
            "reason": ";".join(sorted(set(unsafe_reasons))),
            "n_literature_answerable_issues": int(selected["issue_id"].nunique()),
            "n_allowlisted_targets": int(selected["allowlisted_target"].nunique()),
            "raw_names_emitted": False,
            "mapping_sources_emitted": False,
            "patient_values_or_identifiers_emitted": False,
            "paths_or_locators_emitted": False,
        }
        markdown = (
            "# Targeted OpenEvidence follow-up\n\n"
            "Status: `NOT_GENERATED_SAFETY_GATE_FAILED`.\n\n"
            "A literature-answerable candidate existed, but its descriptions or units did not pass the "
            "prespecified non-PHI/locator screen. No copy-ready prompt was generated.\n"
        )
        return markdown, gate

    sections: list[str] = []
    for (issue_id, target), group in selected.groupby(
        ["issue_id", "allowlisted_target"], sort=True, dropna=False
    ):
        descriptions = sorted(set(group["raw_description"].astype(str)))
        native_units = sorted(set(group["native_unit"].astype(str)))
        normalized_units = sorted(set(group["normalized_unit"].astype(str)))
        sections.extend(
            [
                f"Ambiguity ID: {issue_id}",
                f"Exact repository canonical target: {target}",
                f"Exact non-PHI source descriptions: {json.dumps(descriptions, ensure_ascii=False)}",
                f"Native units: {json.dumps(native_units, ensure_ascii=False)}",
                f"Normalized units: {json.dumps(normalized_units, ensure_ascii=False)}",
                f"Precise evidence question: {LITERATURE_QUESTION_BANK[str(issue_id)]}",
                "",
            ]
        )

    prompt_lines = [
        "# Targeted OpenEvidence follow-up",
        "",
        "Status: `COPY_READY_AFTER_AUTOMATED_AGGREGATE_SAFETY_GATE`.",
        "",
        "BEGIN COPY-READY PROMPT",
        "",
        "We need a targeted clinical measurement-methodology evidence review for the exact MIMIC-IV-ECHO "
        "metadata constructs below. Project metadata has already resolved the dataset identity represented in each "
        "item. Do not infer that any additional project field exists, do not decide whether project fields are "
        "aliases from their names, and do not alter any exact repository identifier.",
        "",
        *sections,
        "For each ambiguity, use this evidence hierarchy: current professional guideline or consensus statement; "
        "peer-reviewed measurement-methodology study; peer-reviewed construct-specific reproducibility study. "
        "Separate guideline fact, deterministic formula inference, and expert inference. Do not convert population "
        "reference-range variation into reader-repeatability evidence. If direct evidence is unavailable or the exact "
        "construct is not supported by the supplied metadata, return UNRESOLVED rather than guessing.",
        "",
        "Return one compact CSV row per ambiguity with exactly these columns:",
        "issue_id,target,metadata_interpretation,claim,evidence_tier,professional_source,doi,pmid,direct_link,"
        "measurement_method,reproducibility_metric,reproducibility_population,certainty,assumptions,remaining_unresolved",
        "",
        "The `issue_id` and `target` values must be copied exactly and may not be repaired or renamed. Map every "
        "citation to one specific claim. Prefer primary professional sources and primary measurement-methodology "
        "studies; do not repeat broad background searches merely to increase citation count.",
        "",
        "END COPY-READY PROMPT",
        "",
    ]
    markdown = "\n".join(prompt_lines)
    gate = {
        "audit": "openevidence_targeted_followup_safety_gate",
        "status": "PASS_COPY_READY",
        "safety_gate_passed": True,
        "prompt_generated": True,
        "reason": "LITERATURE_ANSWERABLE_METADATA_PASSED_SCREEN",
        "n_literature_answerable_issues": int(selected["issue_id"].nunique()),
        "n_allowlisted_targets": int(selected["allowlisted_target"].nunique()),
        "raw_names_emitted": False,
        "mapping_sources_emitted": False,
        "patient_values_or_identifiers_emitted": False,
        "paths_or_locators_emitted": False,
    }
    return markdown, gate


def build_ambiguity_counts(issues: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "issue_id",
        "evidence_type",
        "resolution_status",
        "n_restricted_rows",
        "n_affected_allowlisted_targets",
        "clinician_question_required",
        "literature_followup_required",
        "technical_review_required",
        "value_distribution_audit_required",
        "reason_code",
    ]
    return pd.DataFrame(
        [{column: issue[column] for column in columns} for issue in issues],
        columns=columns,
    )


def build_schema_summary(
    source: pd.DataFrame,
    resolved_columns: dict[str, str | None],
    rows: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "audit": "clinical_metadata_schema",
        "status": "COMPLETE",
        "n_source_rows": int(len(source)),
        "n_source_columns": int(len(source.columns)),
        "n_selected_restricted_rows": int(len(rows)),
        "required_field_presence": {
            key: value is not None
            for key, value in resolved_columns.items()
        },
        "raw_values_emitted": False,
        "source_column_names_emitted": False,
        "patient_or_study_identifiers_read": False,
        "model_outputs_read": False,
        "evidence_classification_vocabulary": sorted(EVIDENCE_TYPES),
        "n_required_issue_classes": len(REQUIRED_ISSUE_IDS),
        "restricted_and_aggregate_outputs_separated": True,
    }


def _sensitive_values(rows: pd.DataFrame) -> set[str]:
    sensitive: set[str] = set()
    for column in (
        "raw_name",
        "raw_description",
        "canonical_source",
    ):
        for value in rows[column].dropna().astype(str):
            text = value.strip()
            if len(text) >= 8 and text not in ALLOWED_TARGET_SET:
                sensitive.add(text)
    for value in rows.loc[
        rows["canonical_mapping_status"] == "SOURCE_CANDIDATE_NOT_ALLOWLISTED",
        "canonical_mapping",
    ].dropna().astype(str):
        if len(value.strip()) >= 8:
            sensitive.add(value.strip())
    return sensitive


def _json_leaf_text_values(payload: Any) -> Iterable[str]:
    """Yield serialized leaf values without treating JSON keys as emitted data."""
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _json_leaf_text_values(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            yield from _json_leaf_text_values(value)
    elif payload is not None:
        yield str(payload)


def _frame_cell_text_values(frame: pd.DataFrame) -> Iterable[str]:
    """Yield DataFrame cell values without treating column headers as data."""
    for value in frame.to_numpy(dtype=object).ravel():
        if value is None or value is pd.NA:
            continue
        try:
            if bool(pd.isna(value)):
                continue
        except (TypeError, ValueError):
            pass
        yield str(value)


def validate_aggregate_outputs(
    schema_summary: dict[str, Any],
    ambiguity: pd.DataFrame,
    unit_summary: pd.DataFrame,
    alias_summary: pd.DataFrame,
    restricted_rows: pd.DataFrame,
) -> list[str]:
    issues: list[str] = []
    try:
        assert_aggregate_safe_json(schema_summary)
        for frame in (ambiguity, unit_summary, alias_summary):
            assert_aggregate_safe_columns(frame)
            if {"raw_name", "raw_description", "canonical_source", "native_unit", "normalized_unit"} & set(frame.columns):
                raise ValueError("Aggregate output contains restricted clinical metadata columns")
    except ValueError:
        issues.append("shared_aggregate_safety_validation_failed")
    if set(unit_summary["target"]) != ALLOWED_TARGET_SET:
        issues.append("unit_summary_target_allowlist_mismatch")
    if set(alias_summary["target"]) != ALLOWED_TARGET_SET:
        issues.append("alias_summary_target_allowlist_mismatch")
    if not set(ambiguity["evidence_type"]).issubset(EVIDENCE_TYPES):
        issues.append("invalid_evidence_type")
    if ambiguity["issue_id"].duplicated().any():
        issues.append("duplicate_issue_id")
    if not REQUIRED_ISSUE_IDS.issubset(set(ambiguity["issue_id"])):
        issues.append("required_issue_class_missing")
    aggregate_values = list(_json_leaf_text_values(schema_summary))
    for frame in (ambiguity, unit_summary, alias_summary):
        aggregate_values.extend(_frame_cell_text_values(frame))
    # Exact canonical identifiers are sanctioned aggregate values.  Their
    # authority is enforced independently by the unit/alias target-set checks
    # above; a short raw alias may legitimately be a substring of one of them.
    aggregate_values = [value for value in aggregate_values if value not in ALLOWED_TARGET_SET]
    sensitive_values = _sensitive_values(restricted_rows)
    if any(
        sensitive_value in aggregate_value
        for sensitive_value in sensitive_values
        for aggregate_value in aggregate_values
    ):
        issues.append("restricted_metadata_value_in_aggregate_payload")
    return issues


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping-csv", type=Path, required=True)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--restricted-output-dir", type=Path, required=True)
    parser.add_argument("--aggregate-output-dir", type=Path, required=True)
    parser.add_argument(
        "--followup-output-dir",
        type=Path,
        help="Separate empty directory for the gated OpenEvidence prompt and its safety gate",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = requested_targets(args.target)
    if not args.mapping_csv.is_file():
        raise FileNotFoundError("Mapping input is missing")
    restricted_dir = require_restricted_path(args.restricted_output_dir)
    aggregate_dir = require_restricted_path(args.aggregate_output_dir)
    if restricted_dir == aggregate_dir:
        raise ValueError("Restricted and aggregate output directories must be different")
    followup_dir = require_restricted_path(args.followup_output_dir) if args.followup_output_dir else None
    output_dirs = [restricted_dir, aggregate_dir] + ([followup_dir] if followup_dir else [])
    if len(set(output_dirs)) != len(output_dirs):
        raise ValueError("Restricted, aggregate, and follow-up output directories must be different")
    if any(any(path.iterdir()) for path in output_dirs):
        raise ValueError("Clinical metadata output directories must all be empty")

    source = load_table(args.mapping_csv)
    resolved_columns = resolve_metadata_columns(source)
    rows = build_review_rows(source, targets)
    summary = build_canonical_summary(rows, targets)
    present_allowlisted_targets = set(
        rows["allowlisted_target"].replace("", pd.NA).dropna().astype(str)
    )
    missing_allowlisted_targets = sorted(set(targets) - present_allowlisted_targets)

    unit_summary = build_unit_summary(rows, targets)
    alias_summary = build_alias_summary(rows, targets)
    issues = classify_unresolved_questions(rows, unit_summary)
    ambiguity = build_ambiguity_counts(issues)
    issue_details = build_restricted_issue_details(issues, rows)
    literature_candidates = build_literature_candidates(issues, rows)
    questionnaire = build_clinician_questionnaire(issues, rows)
    schema_summary = build_schema_summary(source, resolved_columns, rows)
    schema_summary.update(
        {
            "status": (
                "COMPLETE_WITH_MISSING_ALLOWLISTED_TARGETS"
                if missing_allowlisted_targets
                else "COMPLETE"
            ),
            "n_allowlisted_targets_requested": len(targets),
            "n_allowlisted_targets_present": len(present_allowlisted_targets),
            "missing_allowlisted_targets": missing_allowlisted_targets,
            "all_allowlisted_targets_present": not missing_allowlisted_targets,
            "missing_target_mapping_is_registry_authority": False,
        }
    )
    safety_issues = validate_aggregate_outputs(
        schema_summary,
        ambiguity,
        unit_summary,
        alias_summary,
        rows,
    )
    if safety_issues:
        raise ValueError("Aggregate clinical metadata outputs did not pass the safety gate")

    row_path = restricted_dir / "clinical_metadata_review_rows_restricted.csv"
    summary_path = restricted_dir / "clinical_metadata_canonical_summary_restricted.csv"
    issue_path = restricted_dir / "clinical_metadata_unresolved_questions_restricted.csv"
    literature_path = restricted_dir / "clinical_metadata_literature_candidates_restricted.csv"
    questionnaire_path = restricted_dir / "clinical_metadata_clinician_questionnaire_restricted.md"
    rows.to_csv(row_path, index=False)
    summary.to_csv(summary_path, index=False)
    issue_details.to_csv(issue_path, index=False)
    literature_candidates.to_csv(literature_path, index=False)
    questionnaire_path.write_text(questionnaire + ("" if questionnaire.endswith("\n") else "\n"))

    schema_path = aggregate_dir / "clinical_metadata_schema_summary.json"
    ambiguity_path = aggregate_dir / "clinical_metadata_ambiguity_counts.csv"
    unit_path = aggregate_dir / "clinical_metadata_unit_summary.csv"
    alias_path = aggregate_dir / "clinical_metadata_alias_summary.csv"
    schema_path.write_text(json.dumps(schema_summary, indent=2, sort_keys=True) + "\n")
    ambiguity.to_csv(ambiguity_path, index=False)
    unit_summary.to_csv(unit_path, index=False)
    alias_summary.to_csv(alias_path, index=False)

    expected_aggregate_files = {
        "clinical_metadata_review_packet_manifest.json",
        "clinical_metadata_schema_summary.json",
        "clinical_metadata_ambiguity_counts.csv",
        "clinical_metadata_unit_summary.csv",
        "clinical_metadata_alias_summary.csv",
        "clinical_metadata_safety_gate.json",
    }

    completion_status = (
        "COMPLETE_WITH_MISSING_ALLOWLISTED_TARGETS"
        if missing_allowlisted_targets
        else "COMPLETE"
    )
    manifest = {
        "audit": "clinical_metadata_review_packet",
        "status": completion_status,
        "source": {
            "alias": "raw_to_canonical_mapping",
            "bytes": int(args.mapping_csv.stat().st_size),
            "sha256": sha256_file(args.mapping_csv),
        },
        "allowlist_version": "lvef-plus-legacy29-v1",
        "n_allowlisted_targets_requested": len(targets),
        "n_packet_rows": int(len(rows)),
        "n_allowlisted_targets_present": len(present_allowlisted_targets),
        "missing_allowlisted_targets": missing_allowlisted_targets,
        "all_allowlisted_targets_present": not missing_allowlisted_targets,
        "missing_target_mapping_is_registry_authority": False,
        "n_candidate_canonical_mappings_outside_allowlist": int(
            rows.loc[rows["canonical_mapping_status"] == "SOURCE_CANDIDATE_NOT_ALLOWLISTED", "canonical_mapping"].nunique()
        ),
        "n_unresolved_questions": int((ambiguity["resolution_status"] == "UNRESOLVED").sum()),
        "n_clinician_questions": int(ambiguity["clinician_question_required"].sum()),
        "n_literature_answerable_questions": int(ambiguity["literature_followup_required"].sum()),
        "expected_aggregate_output_files": sorted(expected_aggregate_files),
        "restricted_output_files": [
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
            {
                "relative_path": issue_path.name,
                "bytes": int(issue_path.stat().st_size),
                "sha256": sha256_file(issue_path),
            },
            {
                "relative_path": literature_path.name,
                "bytes": int(literature_path.stat().st_size),
                "sha256": sha256_file(literature_path),
            },
            {
                "relative_path": questionnaire_path.name,
                "bytes": int(questionnaire_path.stat().st_size),
                "sha256": sha256_file(questionnaire_path),
            },
        ],
        "aggregate_output_files": [
            {
                "relative_path": path.name,
                "bytes": int(path.stat().st_size),
                "sha256": sha256_file(path),
            }
            for path in (schema_path, ambiguity_path, unit_path, alias_path)
        ],
        "contains_patient_values": False,
        "contains_patient_or_study_identifiers": False,
        "contains_paths_or_locators": False,
        "canonical_candidates_outside_allowlist_are_authority": False,
        "model_fitting_performed": False,
        "test_performance_accessed": False,
    }
    assert_aggregate_safe_json(manifest)
    manifest_path = aggregate_dir / "clinical_metadata_review_packet_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    observed_before_gate = {path.name for path in aggregate_dir.iterdir() if path.is_file()}
    inventory_valid_before_gate = observed_before_gate == expected_aggregate_files - {
        "clinical_metadata_safety_gate.json"
    }
    safety_gate = {
        "audit": "clinical_metadata_aggregate_safety_gate",
        "status": "PASS" if inventory_valid_before_gate else "FAIL",
        "safety_gate_passed": inventory_valid_before_gate,
        "n_issues": 0 if inventory_valid_before_gate else 1,
        "aggregate_file_inventory_exact": inventory_valid_before_gate,
        "n_aggregate_files_expected": len(expected_aggregate_files),
        "n_aggregate_files_before_gate": len(observed_before_gate),
        "n_aggregate_files_after_gate": len(expected_aggregate_files),
        "restricted_raw_rows_emitted_to_aggregate": False,
        "restricted_descriptions_emitted_to_aggregate": False,
        "restricted_units_emitted_to_aggregate": False,
        "patient_values_emitted": False,
        "identifiers_or_locators_emitted": False,
        "model_outputs_read": False,
        "model_fitting_performed": False,
        "test_performance_accessed": False,
    }
    assert_aggregate_safe_json(safety_gate)
    safety_path = aggregate_dir / "clinical_metadata_safety_gate.json"
    safety_path.write_text(json.dumps(safety_gate, indent=2, sort_keys=True) + "\n")
    inventory_valid_after_gate = {
        path.name for path in aggregate_dir.iterdir() if path.is_file()
    } == expected_aggregate_files
    if not inventory_valid_before_gate or not inventory_valid_after_gate:
        safety_gate["status"] = "FAIL"
        safety_gate["safety_gate_passed"] = False
        safety_gate["n_issues"] = 1
        safety_gate["aggregate_file_inventory_exact"] = False
        safety_gate["n_aggregate_files_after_gate"] = len(
            {path.name for path in aggregate_dir.iterdir() if path.is_file()}
        )
        safety_path.write_text(json.dumps(safety_gate, indent=2, sort_keys=True) + "\n")
        raise ValueError("Aggregate output inventory does not match the six-file allowlist")

    followup_status = "NOT_REQUESTED"
    if followup_dir is not None:
        followup_markdown, followup_gate = build_targeted_followup_prompt(literature_candidates)
        assert_aggregate_safe_json(followup_gate)
        followup_prompt_path = followup_dir / "openevidence_targeted_followup_prompt_generated.md"
        followup_gate_path = followup_dir / "openevidence_targeted_followup_safety_gate.json"
        followup_prompt_path.write_text(
            followup_markdown + ("" if followup_markdown.endswith("\n") else "\n")
        )
        followup_gate_path.write_text(json.dumps(followup_gate, indent=2, sort_keys=True) + "\n")
        expected_followup_files = {
            "openevidence_targeted_followup_prompt_generated.md",
            "openevidence_targeted_followup_safety_gate.json",
        }
        if {path.name for path in followup_dir.iterdir() if path.is_file()} != expected_followup_files:
            raise ValueError("Follow-up output inventory does not match the two-file allowlist")
        if not followup_gate["safety_gate_passed"]:
            raise ValueError("Targeted OpenEvidence prompt did not pass its safety gate")
        followup_status = str(followup_gate["status"])

    # stdout is aggregate-only and never prints raw names, descriptions, units,
    # paths, or candidate canonical strings.
    stdout = {
        "audit": manifest["audit"],
        "status": manifest["status"],
        "safety_gate_passed": True,
        "n_allowlisted_targets_present": manifest["n_allowlisted_targets_present"],
        "n_packet_rows": manifest["n_packet_rows"],
        "n_unresolved_questions": manifest["n_unresolved_questions"],
        "n_clinician_questions": manifest["n_clinician_questions"],
        "n_literature_answerable_questions": manifest["n_literature_answerable_questions"],
        "targeted_followup_status": followup_status,
        "restricted_metadata_printed": False,
        "model_fitting_performed": False,
        "test_performance_accessed": False,
    }
    print(json.dumps(stdout, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
