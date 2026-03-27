#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

import pandas as pd


def load_table(path: Path) -> pd.DataFrame:
    suffixes = "".join(path.suffixes)
    if suffixes.endswith(".csv.gz"):
        return pd.read_csv(path, compression="gzip", low_memory=False)
    return pd.read_csv(path, low_memory=False)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]
    return df


def summarize_table(name: str, df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {
        "table": name,
        "rows": int(len(df)),
        "columns": list(df.columns),
    }
    for candidate in ["subject_id", "study_id", "measurement_id", "note_id", "test_type", "dicom_filepath"]:
        if candidate in df.columns:
            out[f"unique_{candidate}"] = int(df[candidate].nunique(dropna=True))
    return out


def build_study_manifest(
    record_df: pd.DataFrame,
    study_df: pd.DataFrame,
    measurement_df: pd.DataFrame | None,
) -> pd.DataFrame:
    group = record_df.groupby("study_id", dropna=False).agg(
        subject_id=("subject_id", "first"),
        n_dicoms=("dicom_filepath", "count"),
        first_acquisition_datetime=("acquisition_datetime", "min"),
        last_acquisition_datetime=("acquisition_datetime", "max"),
    ).reset_index()

    manifest = group.merge(study_df, on="study_id", how="left", suffixes=("", "_study"))

    if measurement_df is not None and "measurement_id" in study_df.columns and "measurement_id" in measurement_df.columns:
        measurement_cols = ["measurement_id"]
        for col in ["measurement_datetime", "test_type"]:
            if col in measurement_df.columns:
                measurement_cols.append(col)
        manifest = manifest.merge(
            measurement_df[measurement_cols].drop_duplicates(),
            on="measurement_id",
            how="left",
            suffixes=("", "_measurement"),
        )

    if "measurement_id" in manifest.columns:
        manifest["has_measurement_link"] = manifest["measurement_id"].notna()
    if "note_id" in manifest.columns:
        manifest["has_note_link"] = manifest["note_id"].notna()

    return manifest


def compute_summary(
    record_df: pd.DataFrame,
    study_df: pd.DataFrame,
    measurement_df: pd.DataFrame | None,
    study_manifest: pd.DataFrame,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "record_table": summarize_table("echo-record-list", record_df),
        "study_table": summarize_table("echo-study-list", study_df),
        "manifest_rows": int(len(study_manifest)),
        "dicoms_per_study": {
            "mean": float(study_manifest["n_dicoms"].mean()),
            "median": float(study_manifest["n_dicoms"].median()),
            "p10": float(study_manifest["n_dicoms"].quantile(0.10)),
            "p90": float(study_manifest["n_dicoms"].quantile(0.90)),
            "max": int(study_manifest["n_dicoms"].max()),
        },
    }

    if "has_measurement_link" in study_manifest.columns:
        summary["measurement_linkage_rate"] = float(study_manifest["has_measurement_link"].mean())
    if "has_note_link" in study_manifest.columns:
        summary["note_linkage_rate"] = float(study_manifest["has_note_link"].mean())

    if measurement_df is not None:
        summary["measurement_table"] = summarize_table("structured_measurement", measurement_df)
        if "test_type" in measurement_df.columns:
            counts = measurement_df["test_type"].value_counts(dropna=False).to_dict()
            summary["measurement_test_type_counts"] = {str(k): int(v) for k, v in counts.items()}

    return summary


def find_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect MIMIC-IV-ECHO metadata and build a study manifest.")
    parser.add_argument("--data-root", type=Path, default=Path.cwd(), help="Directory containing metadata files.")
    parser.add_argument("--record-list", type=Path, default=None, help="Path to echo-record-list.csv")
    parser.add_argument("--study-list", type=Path, default=None, help="Path to echo-study-list.csv")
    parser.add_argument(
        "--measurement-table",
        type=Path,
        default=None,
        help="Path to structured measurement CSV. Supports .csv and .csv.gz names.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs" / "mimic_echo_metadata",
        help="Directory for summary outputs.",
    )
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    record_path = args.record_list or find_existing(
        [
            data_root / "echo-record-list.csv",
            data_root / "echo_record_list.csv",
        ]
    )
    study_path = args.study_list or find_existing(
        [
            data_root / "echo-study-list.csv",
            data_root / "echo_study_list.csv",
        ]
    )
    measurement_path = args.measurement_table or find_existing(
        [
            data_root / "structured-measurement.csv.gz",
            data_root / "structured_measurement.csv.gz",
            data_root / "structured-measurement.csv",
            data_root / "structured_measurement.csv",
        ]
    )

    if record_path is None:
        raise FileNotFoundError("Could not find echo-record-list CSV.")
    if study_path is None:
        raise FileNotFoundError("Could not find echo-study-list CSV.")

    record_df = normalize_columns(load_table(record_path))
    study_df = normalize_columns(load_table(study_path))
    measurement_df = normalize_columns(load_table(measurement_path)) if measurement_path is not None else None

    study_manifest = build_study_manifest(record_df, study_df, measurement_df)
    summary = compute_summary(record_df, study_df, measurement_df, study_manifest)
    summary["resolved_paths"] = {
        "record_list": str(record_path),
        "study_list": str(study_path),
        "measurement_table": str(measurement_path) if measurement_path is not None else None,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "summary.json"
    manifest_csv_path = args.output_dir / "study_manifest.csv"

    summary_path.write_text(json.dumps(summary, indent=2))
    study_manifest.to_csv(manifest_csv_path, index=False)

    print(json.dumps(summary, indent=2))
    print(f"[written] {summary_path}")
    print(f"[written] {manifest_csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
