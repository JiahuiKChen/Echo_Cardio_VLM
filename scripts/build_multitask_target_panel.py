#!/usr/bin/env python3
"""Build a multitask target panel from selected structured-measurement tasks.

Inputs:
- structured_measurements.csv (long format)
- selected_measurement_tasks.csv (from build_measurement_task_registry.py)
- optional measurement_to_canonical_mapping.csv (preferred for stable mapping)
- optional all_eligible_studies.csv and subject_split_map_v1.csv

Outputs:
- multitask_panel_wide.csv        (one row per study)
- multitask_panel_long.csv        (one row per study-task with numeric target)
- multitask_task_metadata.csv     (support + missingness + unit diagnostics)
- multitask_task_panel.summary.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


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
    "m / s": "m/s",
    "m per s": "m/s",
    "m per sec": "m/s",
    "m/sec": "m/s",
    "m second": "m/s",
    "m s": "m/s",
    "cm/s": "cm/s",
    "cm / s": "cm/s",
    "cm per s": "cm/s",
    "cm per sec": "cm/s",
    "cm/sec": "cm/s",
    "cm second": "cm/s",
    "cm s": "cm/s",
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


# value * factor -> canonical_unit value
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
    parser.add_argument("--selected-tasks-csv", type=Path, required=True)
    parser.add_argument(
        "--mapping-csv",
        type=Path,
        default=None,
        help="Optional mapping csv from build_measurement_task_registry.py",
    )
    parser.add_argument(
        "--reference-studies-csv",
        type=Path,
        default=None,
        help="Optional study list used as panel denominator (e.g., all_eligible_studies.csv).",
    )
    parser.add_argument(
        "--subject-split-map-csv",
        type=Path,
        default=None,
        help="Optional subject split map to attach split labels.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-prefix", default="task__")
    parser.add_argument(
        "--min-studies-with-value",
        type=int,
        default=150,
        help="Final panel filter on studies with numeric values per task.",
    )
    parser.add_argument(
        "--min-rows-with-value",
        type=int,
        default=200,
        help="Final panel filter on total numeric rows per task.",
    )
    parser.add_argument(
        "--drop-unknown-preferred-unit",
        action="store_true",
        help="Drop tasks whose preferred unit is 'unknown'.",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[_/]+", " ", text)
    text = re.sub(r"[^a-z0-9%+\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_unit(value: Any) -> str:
    if pd.isna(value):
        return "unknown"
    text = str(value).strip().lower()
    text = text.replace("μ", "u").replace("µ", "u")
    text = re.sub(r"[^a-z0-9/%^.\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "unknown"
    text = text.replace("per second", "per sec")
    text = text.replace("meters", "meter").replace("centimeters", "centimeter").replace("millimeters", "millimeter")
    text = text.replace("square meter", "m2").replace("square centimeter", "cm2")
    text = text.replace("meter^2", "m2").replace("centimeter^2", "cm2")
    if text in UNIT_ALIASES:
        return UNIT_ALIASES[text]
    if "%" in text:
        return "%"
    if text in {"mmhg", "mm hg"}:
        return "mmhg"
    if text in {"cmh2o", "cm h2o"}:
        return "cmh2o"
    return text


def task_col_name(prefix: str, canonical_measurement: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", canonical_measurement).strip("_")
    safe = re.sub(r"_+", "_", safe)
    return f"{prefix}{safe}"


def build_mapping_from_csv(mapping_csv: Path) -> pd.DataFrame:
    mapping = pd.read_csv(mapping_csv)
    required = {"measurement", "canonical_measurement"}
    missing = required - set(mapping.columns)
    if missing:
        raise ValueError(f"mapping-csv missing required columns: {sorted(missing)}")
    # Keep one canonical label per raw measurement.
    mapping = mapping[["measurement", "canonical_measurement"]].dropna()
    mapping = mapping.drop_duplicates(subset=["measurement", "canonical_measurement"])
    ambiguous = mapping.groupby("measurement")["canonical_measurement"].nunique()
    ambiguous_names = ambiguous[ambiguous > 1].index.tolist()
    if ambiguous_names:
        # deterministic fallback to first canonical by lexical order
        mapping = mapping.sort_values(["measurement", "canonical_measurement"])
    mapping = mapping.drop_duplicates(subset=["measurement"], keep="first")
    return mapping


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    selected = pd.read_csv(args.selected_tasks_csv)
    required_task_cols = {"canonical_measurement", "recommended_canonical_unit"}
    missing_task_cols = required_task_cols - set(selected.columns)
    if missing_task_cols:
        raise ValueError(f"selected-tasks-csv missing required columns: {sorted(missing_task_cols)}")

    selected = selected.copy()
    selected["canonical_measurement"] = selected["canonical_measurement"].astype(str)
    selected["recommended_canonical_unit"] = selected["recommended_canonical_unit"].fillna("unknown").astype(str)
    selected["task_col"] = selected["canonical_measurement"].map(lambda x: task_col_name(args.task_prefix, x))

    if args.drop_unknown_preferred_unit:
        selected = selected[selected["recommended_canonical_unit"] != "unknown"].reset_index(drop=True)
        if selected.empty:
            raise RuntimeError("No selected tasks remain after --drop-unknown-preferred-unit.")

    selected_canon = set(selected["canonical_measurement"].tolist())
    preferred_unit_map = dict(
        selected[["canonical_measurement", "recommended_canonical_unit"]].itertuples(index=False, name=None)
    )
    task_col_map = dict(selected[["canonical_measurement", "task_col"]].itertuples(index=False, name=None))

    measures = pd.read_csv(args.measurements_csv)
    needed_measure_cols = {"subject_id", "study_id", "measurement", "result"}
    missing_measures_cols = needed_measure_cols - set(measures.columns)
    if missing_measures_cols:
        raise ValueError(f"measurements-csv missing required columns: {sorted(missing_measures_cols)}")
    if "unit" not in measures.columns:
        measures["unit"] = ""

    # Canonical mapping.
    if args.mapping_csv is not None:
        mapping = build_mapping_from_csv(args.mapping_csv)
        measures = measures.merge(mapping, how="left", on="measurement")
    else:
        measures["canonical_measurement"] = measures["measurement"].map(normalize_text)

    measures["canonical_measurement"] = measures["canonical_measurement"].fillna("").astype(str)
    measures = measures[measures["canonical_measurement"].isin(selected_canon)].copy()
    if measures.empty:
        raise RuntimeError("No measurement rows left after filtering to selected canonical tasks.")

    # Numeric and unit conversion.
    measures["result_numeric"] = pd.to_numeric(measures["result"], errors="coerce")
    measures["unit_norm"] = measures["unit"].map(normalize_unit)
    conv = measures["unit_norm"].map(lambda u: UNIT_TO_CANONICAL.get(u, ("unknown", 1.0)))
    measures["canonical_value_unit"] = conv.map(lambda x: x[0])
    measures["canonical_value_factor"] = conv.map(lambda x: x[1]).astype(float)
    measures["result_canonical"] = measures["result_numeric"] * measures["canonical_value_factor"]

    measures["preferred_unit"] = measures["canonical_measurement"].map(preferred_unit_map)
    measures["preferred_unit"] = measures["preferred_unit"].fillna("unknown")

    # Keep numeric values compatible with task preferred units.
    # If preferred unit unknown, keep numeric raw value as-is.
    measures["target_value"] = np.nan
    unknown_pref = measures["preferred_unit"] == "unknown"
    measures.loc[unknown_pref, "target_value"] = measures.loc[unknown_pref, "result_numeric"]

    known_pref = ~unknown_pref
    compatible = known_pref & (measures["canonical_value_unit"] == measures["preferred_unit"])
    measures.loc[compatible, "target_value"] = measures.loc[compatible, "result_canonical"]

    measures["task_col"] = measures["canonical_measurement"].map(task_col_map)

    valid = measures.dropna(subset=["target_value"]).copy()
    if valid.empty:
        raise RuntimeError("No numeric target values remain after preferred-unit compatibility filter.")

    # Long panel: one row per study-task (median across repeated rows).
    long_panel = (
        valid.groupby(["subject_id", "study_id", "canonical_measurement", "task_col"], as_index=False)
        .agg(
            task_value=("target_value", "median"),
            n_rows_used=("target_value", "count"),
            preferred_unit=("preferred_unit", "first"),
        )
    )

    # Base study index for wide panel.
    if args.reference_studies_csv is not None:
        ref = pd.read_csv(args.reference_studies_csv)
        if "study_id" not in ref.columns:
            raise ValueError("reference-studies-csv must contain study_id")
        if "subject_id" not in ref.columns:
            # fallback subject lookup from measures
            study_subj = (
                measures[["study_id", "subject_id"]]
                .dropna(subset=["study_id", "subject_id"])
                .drop_duplicates(subset=["study_id"])
            )
            ref = ref.merge(study_subj, how="left", on="study_id")
        base = ref[["subject_id", "study_id"]].drop_duplicates(subset=["study_id"]).copy()
    else:
        base = (
            measures[["subject_id", "study_id"]]
            .dropna(subset=["subject_id", "study_id"])
            .drop_duplicates(subset=["study_id"])
            .copy()
        )

    wide = long_panel.pivot(index="study_id", columns="task_col", values="task_value").reset_index()
    panel = base.merge(wide, how="left", on="study_id")

    # Attach split labels if provided.
    split_missing_subjects = 0
    if args.subject_split_map_csv is not None:
        split_df = pd.read_csv(args.subject_split_map_csv)
        if not {"subject_id", "split"}.issubset(split_df.columns):
            raise ValueError("subject-split-map-csv must contain subject_id and split")
        split_df = split_df[["subject_id", "split"]].drop_duplicates(subset=["subject_id"])
        panel = panel.merge(split_df, how="left", on="subject_id")
        split_missing_subjects = int(panel["split"].isna().sum())

    # Task metadata from panel and long table.
    n_panel_studies = int(panel["study_id"].nunique())
    task_meta_rows: list[dict[str, Any]] = []
    for _, task in selected.iterrows():
        canonical = task["canonical_measurement"]
        tcol = task_col_map[canonical]
        if tcol in panel.columns:
            n_studies_with_value = int(panel[tcol].notna().sum())
            values = panel.loc[panel[tcol].notna(), tcol].to_numpy(dtype=float)
        else:
            n_studies_with_value = 0
            values = np.array([], dtype=float)

        long_task = long_panel[long_panel["canonical_measurement"] == canonical]
        n_rows_with_value = int(long_task["n_rows_used"].sum()) if not long_task.empty else 0
        n_subjects_with_value = int(long_task["subject_id"].nunique()) if not long_task.empty else 0
        missing_rate = float(1.0 - (n_studies_with_value / n_panel_studies)) if n_panel_studies > 0 else 1.0

        p05 = float(np.nanpercentile(values, 5)) if values.size else np.nan
        p50 = float(np.nanpercentile(values, 50)) if values.size else np.nan
        p95 = float(np.nanpercentile(values, 95)) if values.size else np.nan

        task_meta_rows.append(
            {
                "canonical_measurement": canonical,
                "task_col": tcol,
                "preferred_unit": task["recommended_canonical_unit"],
                "n_studies_with_value": n_studies_with_value,
                "n_subjects_with_value": n_subjects_with_value,
                "n_rows_with_value": n_rows_with_value,
                "study_coverage_with_value": round(
                    float(n_studies_with_value / n_panel_studies) if n_panel_studies > 0 else 0.0,
                    4,
                ),
                "missing_rate": round(missing_rate, 4),
                "p05": round(p05, 4) if np.isfinite(p05) else np.nan,
                "p50": round(p50, 4) if np.isfinite(p50) else np.nan,
                "p95": round(p95, 4) if np.isfinite(p95) else np.nan,
            }
        )
    task_meta = pd.DataFrame(task_meta_rows).sort_values(
        ["n_studies_with_value", "n_rows_with_value"],
        ascending=False,
    ).reset_index(drop=True)

    # Final support filter on the generated panel.
    keep_mask = (
        (task_meta["n_studies_with_value"] >= args.min_studies_with_value)
        & (task_meta["n_rows_with_value"] >= args.min_rows_with_value)
    )
    kept_tasks = task_meta[keep_mask].copy().reset_index(drop=True)
    dropped_tasks = task_meta[~keep_mask].copy().reset_index(drop=True)

    kept_cols = kept_tasks["task_col"].tolist()
    id_cols = ["subject_id", "study_id"]
    if "split" in panel.columns:
        id_cols.append("split")
    panel_final = panel[id_cols + kept_cols].copy()

    # Add convenience completeness field.
    if kept_cols:
        panel_final["n_tasks_available"] = panel_final[kept_cols].notna().sum(axis=1).astype(int)
    else:
        panel_final["n_tasks_available"] = 0

    long_final = long_panel[long_panel["task_col"].isin(set(kept_cols))].copy()

    # Write outputs.
    wide_csv = args.output_dir / "multitask_panel_wide.csv"
    long_csv = args.output_dir / "multitask_panel_long.csv"
    meta_csv = args.output_dir / "multitask_task_metadata.csv"
    kept_csv = args.output_dir / "multitask_tasks_kept.csv"
    dropped_csv = args.output_dir / "multitask_tasks_dropped.csv"
    summary_json = args.output_dir / "multitask_task_panel.summary.json"

    panel_final.to_csv(wide_csv, index=False)
    long_final.to_csv(long_csv, index=False)
    task_meta.to_csv(meta_csv, index=False)
    kept_tasks.to_csv(kept_csv, index=False)
    dropped_tasks.to_csv(dropped_csv, index=False)

    summary = {
        "measurements_csv": str(args.measurements_csv.resolve()),
        "selected_tasks_csv": str(args.selected_tasks_csv.resolve()),
        "mapping_csv": str(args.mapping_csv.resolve()) if args.mapping_csv else None,
        "reference_studies_csv": str(args.reference_studies_csv.resolve()) if args.reference_studies_csv else None,
        "subject_split_map_csv": str(args.subject_split_map_csv.resolve()) if args.subject_split_map_csv else None,
        "task_prefix": args.task_prefix,
        "n_selected_tasks_input": int(len(selected)),
        "n_tasks_kept_final": int(len(kept_tasks)),
        "n_tasks_dropped_final": int(len(dropped_tasks)),
        "n_panel_rows": int(len(panel_final)),
        "n_panel_studies": int(panel_final["study_id"].nunique()),
        "n_panel_subjects": int(panel_final["subject_id"].nunique()),
        "split_missing_subject_rows": int(split_missing_subjects),
        "min_studies_with_value": int(args.min_studies_with_value),
        "min_rows_with_value": int(args.min_rows_with_value),
        "outputs": {
            "multitask_panel_wide_csv": str(wide_csv.resolve()),
            "multitask_panel_long_csv": str(long_csv.resolve()),
            "multitask_task_metadata_csv": str(meta_csv.resolve()),
            "multitask_tasks_kept_csv": str(kept_csv.resolve()),
            "multitask_tasks_dropped_csv": str(dropped_csv.resolve()),
        },
    }
    summary_json.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {wide_csv.resolve()}")
    print(f"[written] {long_csv.resolve()}")
    print(f"[written] {meta_csv.resolve()}")
    print(f"[written] {kept_csv.resolve()}")
    print(f"[written] {dropped_csv.resolve()}")
    print(f"[written] {summary_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

