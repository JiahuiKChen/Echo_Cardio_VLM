#!/usr/bin/env python3
"""Target-specific structured-measurement leakage audit.

The script classifies measurement/task names only. It does not delete columns,
rewrite inputs, or decide final model features.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


TARGETS = ("tapse", "lvot_vti")

LEAKAGE_RULES: dict[str, list[tuple[str, str]]] = {
    "tapse": [
        (r"\btapse\b", "direct TAPSE field"),
        (r"tricuspid annular plane systolic excursion", "direct TAPSE synonym"),
        (r"\brv systolic function\b|\bright ventricular systolic function\b", "RV systolic function summary"),
        (r"\brv function\b|\bright ventricular function\b", "RV function summary"),
        (r"\bs prime\b|\bs'\b|\bs wave\b|tissue doppler.*s", "RV/tissue Doppler systolic velocity"),
        (r"\bfac\b|fractional area change", "RV fractional area change"),
    ],
    "lvot_vti": [
        (r"\blvot vti\b|\blvot_vti\b", "direct LVOT VTI field"),
        (r"lvot velocity time integral|left ventricular outflow tract velocity time integral", "direct LVOT VTI synonym"),
        (r"\bav vti\b|\bav_vti\b|aortic valve velocity time integral|aortic vti", "AV/aortic valve VTI"),
        (r"stroke volume|\bsv\b", "stroke volume derivative"),
        (r"cardiac output|\bco\b", "cardiac output derivative"),
        (r"cardiac index|\bci\b", "cardiac index derivative"),
        (r"lvot stroke distance|stroke distance", "LVOT stroke-distance derivative"),
    ],
}

SUSPICIOUS_RULES: dict[str, list[tuple[str, str]]] = {
    "tapse": [
        (r"\brv\b|\bright ventricular\b|\btricuspid\b", "RV/tricuspid measurement needing review"),
        (r"pulmonary artery|rvsp|pasp", "hemodynamic RV-adjacent field"),
    ],
    "lvot_vti": [
        (r"\blvot\b|outflow tract", "LVOT-adjacent field needing review"),
        (r"\baortic valve\b|\bav_", "aortic valve field needing review"),
        (r"velocity|vti|time integral", "velocity/VTI-like field needing review"),
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-csv",
        type=Path,
        required=True,
        help="Structured measurement export or measurement/task registry CSV.",
    )
    parser.add_argument("--target", choices=[*TARGETS, "all"], default="all")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[_/]+", " ", text)
    text = re.sub(r"[^a-z0-9%+\-'\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_name_columns(df: pd.DataFrame) -> list[str]:
    candidates = [
        "measurement",
        "canonical_measurement",
        "measurement_description",
        "original_measurements_top10",
        "task_col",
    ]
    return [c for c in candidates if c in df.columns]


def build_field_table(df: pd.DataFrame) -> pd.DataFrame:
    name_cols = detect_name_columns(df)
    if not name_cols:
        raise ValueError(
            "Input must contain at least one name-like column: measurement, "
            "canonical_measurement, measurement_description, original_measurements_top10, or task_col."
        )

    rows: list[dict[str, Any]] = []
    if "measurement" in df.columns:
        group_cols = ["measurement"]
        agg: dict[str, tuple[str, str]] = {}
        if "measurement_description" in df.columns:
            agg["measurement_description"] = ("measurement_description", lambda x: " | ".join(sorted(set(map(str, x.dropna())))[:5]))
        if "unit" in df.columns:
            agg["units"] = ("unit", lambda x: "|".join(sorted(set(map(str, x.dropna())))[:10]))
        if "study_id" in df.columns:
            agg["n_studies"] = ("study_id", "nunique")
        if "subject_id" in df.columns:
            agg["n_subjects"] = ("subject_id", "nunique")
        grouped = df.groupby(group_cols, dropna=False).agg(**agg).reset_index() if agg else df[group_cols].drop_duplicates()
        for _, row in grouped.iterrows():
            rows.append(row.to_dict())
    else:
        fields = df[name_cols].drop_duplicates().copy()
        for _, row in fields.iterrows():
            rows.append(row.to_dict())

    fields_df = pd.DataFrame(rows)
    fields_df["_audit_haystack"] = ""
    for col in name_cols:
        if col in fields_df.columns:
            fields_df["_audit_haystack"] += " " + fields_df[col].map(normalize_text)
    fields_df["_audit_haystack"] = fields_df["_audit_haystack"].str.strip()
    fields_df["field_label"] = (
        fields_df["measurement"].astype(str)
        if "measurement" in fields_df.columns
        else fields_df[name_cols[0]].astype(str)
    )
    return fields_df


def first_matching_rule(text: str, rules: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    for pattern, reason in rules:
        if re.search(pattern, text):
            return pattern, reason
    return None, None


def classify(fields: pd.DataFrame, target: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in fields.iterrows():
        text = str(row["_audit_haystack"])
        pattern, reason = first_matching_rule(text, LEAKAGE_RULES[target])
        status = "exclude"
        if pattern is None:
            pattern, reason = first_matching_rule(text, SUSPICIOUS_RULES[target])
            status = "manual_review" if pattern is not None else "allowed_by_default"
        out = {k: row[k] for k in fields.columns if not k.startswith("_audit_")}
        out.update(
            {
                "target": target,
                "leakage_status": status,
                "matched_pattern": pattern or "",
                "reason": reason or "",
            }
        )
        rows.append(out)
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if not args.input_csv.exists():
        raise FileNotFoundError(f"Missing input CSV: {args.input_csv}")
    df = pd.read_csv(args.input_csv)
    fields = build_field_table(df)

    targets = list(TARGETS) if args.target == "all" else [args.target]
    reports = [classify(fields, target) for target in targets]
    report = pd.concat(reports, ignore_index=True)
    report = report.sort_values(["target", "leakage_status", "field_label"]).reset_index(drop=True)

    excluded = report[report["leakage_status"] == "exclude"].copy()
    suspicious = report[report["leakage_status"] == "manual_review"].copy()
    allowed = report[report["leakage_status"] == "allowed_by_default"].copy()

    report_path = args.output_dir / "target_leakage_report.csv"
    excluded_path = args.output_dir / "excluded_fields.csv"
    suspicious_path = args.output_dir / "suspicious_fields.csv"
    allowed_path = args.output_dir / "allowed_by_default_fields.csv"
    warnings_path = args.output_dir / "target_leakage_warnings.json"

    report.to_csv(report_path, index=False)
    excluded.to_csv(excluded_path, index=False)
    suspicious.to_csv(suspicious_path, index=False)
    allowed.to_csv(allowed_path, index=False)

    warnings = []
    if excluded.empty:
        warnings.append("No excluded fields detected; verify input columns and naming conventions.")
    if suspicious.empty:
        warnings.append("No suspicious fields detected; verify this is expected for the input scope.")

    payload = {
        "input_csv": str(args.input_csv),
        "targets": targets,
        "n_fields_input": int(fields["field_label"].nunique()),
        "n_report_rows": int(len(report)),
        "n_excluded_rows": int(len(excluded)),
        "n_suspicious_rows": int(len(suspicious)),
        "n_allowed_rows": int(len(allowed)),
        "warnings": warnings,
        "notes": [
            "This audit only recommends exclusions and manual-review fields.",
            "It does not modify the source data or create model feature matrices.",
        ],
    }
    warnings_path.write_text(json.dumps(payload, indent=2))

    print(json.dumps(payload, indent=2))
    print(f"[written] {report_path.resolve()}")
    print(f"[written] {excluded_path.resolve()}")
    print(f"[written] {suspicious_path.resolve()}")
    print(f"[written] {allowed_path.resolve()}")
    print(f"[written] {warnings_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
