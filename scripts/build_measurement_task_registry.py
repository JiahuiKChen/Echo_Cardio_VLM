#!/usr/bin/env python3
"""Build a canonical task registry from structured_measurements.csv.

This script is the first step for expanding beyond LVEF:
1) quantify sample size/coverage per measurement
2) aggregate heterogeneous naming variants into canonical tasks
3) normalize unit strings and flag unit-mixing risks
4) select high-support tasks for multi-task modeling
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def to_markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "| Empty |\n|---|\n| No rows |\n"
    cols = [str(c) for c in df.columns]
    lines = [
        "| " + " | ".join(cols) + " |",
        "|" + "|".join(["---"] * len(cols)) + "|",
    ]
    for _, row in df.iterrows():
        vals = []
        for c in df.columns:
            v = row[c]
            if isinstance(v, float):
                vals.append(f"{v:.4f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


AUTO_CANONICAL_RULES: list[tuple[str, str]] = [
    (r"\b(lvef|ef|ejection fraction)\b", "left_ventricular_ejection_fraction"),
    (r"\b(lvedv|lv end diastolic volume|lv edv)\b", "left_ventricular_end_diastolic_volume"),
    (r"\b(lvesv|lv end systolic volume|lv esv)\b", "left_ventricular_end_systolic_volume"),
    (r"\b(lvedd|lvidd|lv end diastolic diameter|lv internal diameter diastole)\b", "left_ventricular_end_diastolic_diameter"),
    (r"\b(lvesd|lvids|lv end systolic diameter|lv internal diameter systole)\b", "left_ventricular_end_systolic_diameter"),
    (r"\b(ivsd|ivs diastolic|septal thickness diastole)\b", "interventricular_septum_diastolic_thickness"),
    (r"\b(lvpwd|posterior wall diastolic|pw diastolic)\b", "left_ventricular_posterior_wall_diastolic_thickness"),
    (r"\b(la volume index|lavi)\b", "left_atrial_volume_index"),
    (r"\b(la volume)\b", "left_atrial_volume"),
    (r"\b(la area)\b", "left_atrial_area"),
    (r"\b(ra area)\b", "right_atrial_area"),
    (r"\b(ra pressure|rap)\b", "right_atrial_pressure"),
    (r"\b(rvsp)\b", "right_ventricular_systolic_pressure"),
    (r"\b(pasp)\b", "pulmonary_artery_systolic_pressure"),
    (r"\b(tr v max|tr velocity|tr jet velocity)\b", "tricuspid_regurgitant_peak_velocity"),
    (r"\b(e\/e('| prime)?|e e prime)\b", "mitral_inflow_to_annular_velocity_ratio"),
    (r"\b(mitral e velocity|mv e)\b", "mitral_e_velocity"),
    (r"\b(mitral a velocity|mv a)\b", "mitral_a_velocity"),
    (r"\b(mitral e a ratio|mv e a)\b", "mitral_e_a_ratio"),
    (r"\b(av peak gradient|aortic valve peak gradient)\b", "aortic_valve_peak_gradient"),
    (r"\b(av mean gradient|aortic valve mean gradient)\b", "aortic_valve_mean_gradient"),
    (r"\b(av area|aortic valve area)\b", "aortic_valve_area"),
    (r"\b(mv mean gradient|mitral valve mean gradient)\b", "mitral_valve_mean_gradient"),
    (r"\b(mv area|mitral valve area)\b", "mitral_valve_area"),
    (r"\b(tapse)\b", "tricuspid_annular_plane_systolic_excursion"),
    (r"\b(s prime|s')\b", "tissue_doppler_systolic_velocity"),
    (r"\b(gls|global longitudinal strain)\b", "global_longitudinal_strain"),
    (r"\b(aortic root)\b", "aortic_root_diameter"),
    (r"\b(ascending aorta)\b", "ascending_aorta_diameter"),
    (r"\b(lv mass index)\b", "left_ventricular_mass_index"),
    (r"\b(lv mass)\b", "left_ventricular_mass"),
]


UNIT_ALIASES = {
    "percent": "%",
    "percentage": "%",
    "%": "%",
    "mm": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "cm": "cm",
    "centimeter": "cm",
    "centimeters": "cm",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "mmhg": "mmhg",
    "mm hg": "mmhg",
    "cmh2o": "cmh2o",
    "cm h2o": "cmh2o",
    "ml": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "m/s": "m/s",
    "m sec": "m/s",
    "cm/s": "cm/s",
    "cm sec": "cm/s",
    "m2": "m2",
    "m^2": "m2",
    "cm2": "cm2",
    "cm^2": "cm2",
    "ml/m2": "ml/m2",
    "ml/m^2": "ml/m2",
    "bpm": "bpm",
    "beats/min": "bpm",
    "ms": "ms",
    "s": "s",
    "sec": "s",
    "seconds": "s",
    "ratio": "ratio",
    "unitless": "ratio",
}


UNIT_CATEGORY = {
    "%": "fraction",
    "ratio": "fraction",
    "mm": "length",
    "cm": "length",
    "m": "length",
    "cm2": "area",
    "m2": "area",
    "ml": "volume",
    "l": "volume",
    "ml/m2": "indexed_volume",
    "m/s": "velocity",
    "cm/s": "velocity",
    "mmhg": "pressure",
    "cmh2o": "pressure",
    "bpm": "rate",
    "ms": "time",
    "s": "time",
    "unknown": "unknown",
}


# Canonical numeric units used for coarse comparability in registry summaries.
# factor = raw_value * factor -> canonical_value
UNIT_TO_CANONICAL: dict[str, tuple[str, float]] = {
    "%": ("%", 1.0),
    "ratio": ("ratio", 1.0),
    "mm": ("mm", 1.0),
    "cm": ("mm", 10.0),
    "m": ("mm", 1000.0),
    "cm2": ("cm2", 1.0),
    "m2": ("cm2", 10000.0),
    "ml": ("ml", 1.0),
    "l": ("ml", 1000.0),
    "ml/m2": ("ml/m2", 1.0),
    "m/s": ("cm/s", 100.0),
    "cm/s": ("cm/s", 1.0),
    "mmhg": ("mmhg", 1.0),
    "cmh2o": ("cmh2o", 1.0),
    "bpm": ("bpm", 1.0),
    "ms": ("ms", 1.0),
    "s": ("ms", 1000.0),
    "unknown": ("unknown", 1.0),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurements-csv", type=Path, required=True)
    parser.add_argument(
        "--reference-studies-csv",
        type=Path,
        default=None,
        help="Optional cohort study manifest with study_id. Used for coverage denominator.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--manual-map-csv",
        type=Path,
        default=None,
        help="Optional CSV with columns: measurement,canonical_measurement",
    )
    parser.add_argument("--min-studies", type=int, default=150)
    parser.add_argument("--min-result-rows", type=int, default=200)
    parser.add_argument("--min-result-rate", type=float, default=0.30)
    parser.add_argument(
        "--max-unit-categories",
        type=int,
        default=1,
        help="Exclude tasks with more than this number of non-unknown unit categories.",
    )
    parser.add_argument(
        "--exclude-regex",
        default="",
        help="Optional regex to exclude canonical tasks (e.g., '^left_ventricular_ejection_fraction$').",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[_/]+", " ", text)
    text = re.sub(r"[^a-z0-9%+\\-\\s]", " ", text)
    text = re.sub(r"\\s+", " ", text).strip()
    return text


def normalize_unit(value: Any) -> str:
    if pd.isna(value):
        return "unknown"
    text = normalize_text(value)
    if not text:
        return "unknown"
    text = text.replace("per second", "/s").replace("per sec", "/s")
    text = text.replace("m sec", "m/s").replace("cm sec", "cm/s")
    if text in UNIT_ALIASES:
        return UNIT_ALIASES[text]
    if "%" in text:
        return "%"
    if text in {"mmhg", "mm hg"}:
        return "mmhg"
    if text in {"cmh2o", "cm h2o"}:
        return "cmh2o"
    return text


def canonicalize_measurement(
    measurement_clean: str,
    description_clean: str,
    manual_map: dict[str, str],
) -> tuple[str, str]:
    if measurement_clean in manual_map:
        return manual_map[measurement_clean], "manual"

    haystack = f"{measurement_clean} {description_clean}".strip()
    for pattern, canonical in AUTO_CANONICAL_RULES:
        if re.search(pattern, haystack):
            return canonical, "auto_rule"

    fallback = measurement_clean.replace(" ", "_")
    fallback = re.sub(r"_+", "_", fallback).strip("_")
    if not fallback:
        fallback = "unknown_measurement"
    return fallback, "fallback"


def load_manual_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    df = pd.read_csv(path)
    required = {"measurement", "canonical_measurement"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"manual-map-csv missing required columns: {sorted(missing)}")
    out: dict[str, str] = {}
    for _, row in df.iterrows():
        raw = normalize_text(row["measurement"])
        canon = str(row["canonical_measurement"]).strip()
        if raw and canon:
            out[raw] = canon
    return out


def series_top_counts(values: pd.Series, limit: int = 5) -> str:
    counts = values.fillna("unknown").astype(str).value_counts().head(limit)
    return ";".join([f"{k}:{int(v)}" for k, v in counts.items()])


def compute_canonical_group(group: pd.DataFrame, total_reference_studies: int) -> dict[str, Any]:
    non_unknown_cat = sorted([x for x in group["unit_category"].unique().tolist() if x != "unknown"])
    non_unknown_units = sorted([x for x in group["unit_norm"].unique().tolist() if x != "unknown"])

    numeric = group.dropna(subset=["result_numeric"]).copy()
    result_rate = float(len(numeric) / len(group)) if len(group) > 0 else 0.0

    canonical_unit = "unknown"
    p05 = p50 = p95 = np.nan
    if not numeric.empty:
        canonical_unit = str(numeric["canonical_value_unit"].value_counts().index[0])
        numeric_same_unit = numeric[numeric["canonical_value_unit"] == canonical_unit]
        if not numeric_same_unit.empty:
            p05 = float(np.nanpercentile(numeric_same_unit["result_canonical"].to_numpy(dtype=float), 5))
            p50 = float(np.nanpercentile(numeric_same_unit["result_canonical"].to_numpy(dtype=float), 50))
            p95 = float(np.nanpercentile(numeric_same_unit["result_canonical"].to_numpy(dtype=float), 95))

    n_studies = int(group["study_id"].nunique())
    study_coverage = float(n_studies / total_reference_studies) if total_reference_studies > 0 else 0.0

    return {
        "n_rows": int(len(group)),
        "n_result_rows": int(len(numeric)),
        "result_rate": round(result_rate, 4),
        "n_studies": n_studies,
        "n_subjects": int(group["subject_id"].nunique()),
        "study_coverage": round(study_coverage, 4),
        "n_original_measurements": int(group["measurement"].nunique()),
        "original_measurements_top10": series_top_counts(group["measurement"], limit=10),
        "unit_categories": ",".join(non_unknown_cat) if non_unknown_cat else "unknown",
        "n_unit_categories": int(len(non_unknown_cat)),
        "unit_norm_top10": series_top_counts(group["unit_norm"], limit=10),
        "recommended_canonical_unit": canonical_unit,
        "p05_canonical": round(p05, 4) if np.isfinite(p05) else np.nan,
        "p50_canonical": round(p50, 4) if np.isfinite(p50) else np.nan,
        "p95_canonical": round(p95, 4) if np.isfinite(p95) else np.nan,
    }


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.measurements_csv)
    required = {"subject_id", "study_id", "measurement", "result"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"measurements-csv missing required columns: {sorted(missing)}")

    if "unit" not in df.columns:
        df["unit"] = ""
    if "measurement_description" not in df.columns:
        df["measurement_description"] = ""

    # Normalize core columns.
    df["measurement_clean"] = df["measurement"].map(normalize_text)
    df["description_clean"] = df["measurement_description"].map(normalize_text)
    df["unit_norm"] = df["unit"].map(normalize_unit)
    df["unit_category"] = df["unit_norm"].map(lambda x: UNIT_CATEGORY.get(x, "unknown"))
    df["result_numeric"] = pd.to_numeric(df["result"], errors="coerce")

    # Canonical value conversion (used for rough distribution summaries only).
    canon_info = df["unit_norm"].map(lambda u: UNIT_TO_CANONICAL.get(u, ("unknown", 1.0)))
    df["canonical_value_unit"] = canon_info.map(lambda x: x[0])
    df["canonical_value_factor"] = canon_info.map(lambda x: x[1]).astype(float)
    df["result_canonical"] = df["result_numeric"] * df["canonical_value_factor"]

    manual_map = load_manual_map(args.manual_map_csv)
    canonical = df.apply(
        lambda row: canonicalize_measurement(
            measurement_clean=row["measurement_clean"],
            description_clean=row["description_clean"],
            manual_map=manual_map,
        ),
        axis=1,
    )
    df["canonical_measurement"] = canonical.map(lambda x: x[0])
    df["canonical_source"] = canonical.map(lambda x: x[1])

    if args.reference_studies_csv is not None:
        ref_df = pd.read_csv(args.reference_studies_csv)
        if "study_id" not in ref_df.columns:
            raise ValueError("reference-studies-csv must contain study_id")
        total_reference_studies = int(ref_df["study_id"].nunique())
    else:
        total_reference_studies = int(df["study_id"].nunique())

    # Per-original measurement summary.
    raw_rows: list[dict[str, Any]] = []
    for measurement, g in df.groupby("measurement", dropna=False):
        numeric = g.dropna(subset=["result_numeric"])
        raw_rows.append(
            {
                "measurement": measurement,
                "measurement_clean": g["measurement_clean"].iloc[0],
                "canonical_measurement": g["canonical_measurement"].iloc[0],
                "canonical_source_top": g["canonical_source"].value_counts().index[0],
                "n_rows": int(len(g)),
                "n_result_rows": int(len(numeric)),
                "result_rate": round(float(len(numeric) / len(g)) if len(g) else 0.0, 4),
                "n_studies": int(g["study_id"].nunique()),
                "n_subjects": int(g["subject_id"].nunique()),
                "unit_norm_top10": series_top_counts(g["unit_norm"], limit=10),
                "unit_categories": ",".join(sorted(set([x for x in g["unit_category"].tolist() if x != "unknown"])))
                or "unknown",
            }
        )
    raw_df = pd.DataFrame(raw_rows).sort_values(["n_studies", "n_rows"], ascending=False).reset_index(drop=True)

    # Per-canonical task summary.
    canonical_rows: list[dict[str, Any]] = []
    for canon_name, g in df.groupby("canonical_measurement", dropna=False):
        row = {"canonical_measurement": canon_name}
        row.update(compute_canonical_group(g, total_reference_studies=total_reference_studies))
        canonical_rows.append(row)
    canonical_df = pd.DataFrame(canonical_rows).sort_values(
        ["n_studies", "n_result_rows", "n_rows"],
        ascending=False,
    ).reset_index(drop=True)

    # Task selection filtering.
    exclude_re = re.compile(args.exclude_regex) if args.exclude_regex else None
    reasons: list[list[str]] = []
    for _, row in canonical_df.iterrows():
        r: list[str] = []
        if int(row["n_studies"]) < args.min_studies:
            r.append("low_study_count")
        if int(row["n_result_rows"]) < args.min_result_rows:
            r.append("low_result_rows")
        if float(row["result_rate"]) < args.min_result_rate:
            r.append("low_result_rate")
        if int(row["n_unit_categories"]) > args.max_unit_categories:
            r.append("mixed_unit_categories")
        if exclude_re and exclude_re.search(str(row["canonical_measurement"])):
            r.append("excluded_by_regex")
        reasons.append(r)

    canonical_df["exclude_reasons"] = [",".join(r) for r in reasons]
    canonical_df["selected_for_multitask"] = [len(r) == 0 for r in reasons]

    selected_df = canonical_df[canonical_df["selected_for_multitask"]].copy().reset_index(drop=True)
    excluded_df = canonical_df[~canonical_df["selected_for_multitask"]].copy().reset_index(drop=True)

    # Save outputs.
    raw_csv = args.output_dir / "measurement_registry_raw.csv"
    canonical_csv = args.output_dir / "measurement_registry_canonical.csv"
    selected_csv = args.output_dir / "selected_measurement_tasks.csv"
    excluded_csv = args.output_dir / "excluded_measurement_tasks.csv"
    mapping_csv = args.output_dir / "measurement_to_canonical_mapping.csv"
    summary_json = args.output_dir / "measurement_task_registry.summary.json"
    summary_md = args.output_dir / "measurement_task_registry.summary.md"

    raw_df.to_csv(raw_csv, index=False)
    canonical_df.to_csv(canonical_csv, index=False)
    selected_df.to_csv(selected_csv, index=False)
    excluded_df.to_csv(excluded_csv, index=False)
    df[
        [
            "measurement",
            "measurement_clean",
            "measurement_description",
            "description_clean",
            "canonical_measurement",
            "canonical_source",
            "unit",
            "unit_norm",
            "unit_category",
        ]
    ].drop_duplicates().to_csv(mapping_csv, index=False)

    summary = {
        "measurements_csv": str(args.measurements_csv.resolve()),
        "reference_studies_csv": str(args.reference_studies_csv.resolve()) if args.reference_studies_csv else None,
        "n_rows": int(len(df)),
        "n_studies_with_any_measurement": int(df["study_id"].nunique()),
        "n_subjects_with_any_measurement": int(df["subject_id"].nunique()),
        "n_unique_raw_measurements": int(df["measurement"].nunique()),
        "n_unique_canonical_measurements": int(canonical_df["canonical_measurement"].nunique()),
        "n_selected_tasks": int(len(selected_df)),
        "n_excluded_tasks": int(len(excluded_df)),
        "selection_thresholds": {
            "min_studies": int(args.min_studies),
            "min_result_rows": int(args.min_result_rows),
            "min_result_rate": float(args.min_result_rate),
            "max_unit_categories": int(args.max_unit_categories),
            "exclude_regex": args.exclude_regex,
        },
        "outputs": {
            "raw_registry_csv": str(raw_csv.resolve()),
            "canonical_registry_csv": str(canonical_csv.resolve()),
            "selected_tasks_csv": str(selected_csv.resolve()),
            "excluded_tasks_csv": str(excluded_csv.resolve()),
            "mapping_csv": str(mapping_csv.resolve()),
        },
    }
    summary_json.write_text(json.dumps(summary, indent=2))

    top_selected = selected_df.head(20)[
        [
            "canonical_measurement",
            "n_studies",
            "study_coverage",
            "n_result_rows",
            "result_rate",
            "recommended_canonical_unit",
        ]
    ]
    top_excluded = excluded_df.head(20)[
        [
            "canonical_measurement",
            "n_studies",
            "n_result_rows",
            "result_rate",
            "n_unit_categories",
            "exclude_reasons",
        ]
    ]

    md_lines = [
        "# Measurement Task Registry Summary",
        "",
        f"- Input rows: `{summary['n_rows']}`",
        f"- Unique raw measurements: `{summary['n_unique_raw_measurements']}`",
        f"- Unique canonical tasks: `{summary['n_unique_canonical_measurements']}`",
        f"- Selected tasks: `{summary['n_selected_tasks']}`",
        f"- Excluded tasks: `{summary['n_excluded_tasks']}`",
        "",
        "## Selection Thresholds",
        "",
        f"- min_studies: `{args.min_studies}`",
        f"- min_result_rows: `{args.min_result_rows}`",
        f"- min_result_rate: `{args.min_result_rate}`",
        f"- max_unit_categories: `{args.max_unit_categories}`",
        f"- exclude_regex: `{args.exclude_regex or '(none)'}`",
        "",
        "## Top Selected Tasks (by study count)",
        "",
        to_markdown_table(top_selected),
        "",
        "## Top Excluded Tasks (by study count)",
        "",
        to_markdown_table(top_excluded),
        "",
    ]
    summary_md.write_text("\n".join(md_lines))

    print(json.dumps(summary, indent=2))
    print(f"[written] {raw_csv.resolve()}")
    print(f"[written] {canonical_csv.resolve()}")
    print(f"[written] {selected_csv.resolve()}")
    print(f"[written] {excluded_csv.resolve()}")
    print(f"[written] {mapping_csv.resolve()}")
    print(f"[written] {summary_json.resolve()}")
    print(f"[written] {summary_md.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
