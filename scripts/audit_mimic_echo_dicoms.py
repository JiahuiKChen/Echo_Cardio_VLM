#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pydicom


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a header-level QC manifest for a MIMIC-IV-ECHO subset.")
    parser.add_argument("--records-csv", type=Path, required=True, help="Path to selected_records.csv")
    parser.add_argument("--data-root", type=Path, required=True, help="Root of the local MIMIC-IV-ECHO download")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for audit outputs")
    return parser.parse_args()


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_ultrasound_region(ds: pydicom.Dataset) -> dict[str, Any]:
    regions = getattr(ds, "SequenceOfUltrasoundRegions", None)
    if not regions:
        return {
            "has_ultrasound_region": False,
            "region_min_x0": None,
            "region_min_y0": None,
            "region_max_x1": None,
            "region_max_y1": None,
        }
    region = regions[0]
    return {
        "has_ultrasound_region": True,
        "region_min_x0": as_int(getattr(region, "RegionLocationMinX0", None)),
        "region_min_y0": as_int(getattr(region, "RegionLocationMinY0", None)),
        "region_max_x1": as_int(getattr(region, "RegionLocationMaxX1", None)),
        "region_max_y1": as_int(getattr(region, "RegionLocationMaxY1", None)),
    }


def audit_one(record: pd.Series, data_root: Path) -> dict[str, Any]:
    relative_path = str(record["dicom_filepath"]).lstrip("/")
    dicom_path = data_root / relative_path
    row = {
        "subject_id": int(record["subject_id"]),
        "study_id": int(record["study_id"]),
        "acquisition_datetime": record.get("acquisition_datetime"),
        "dicom_filepath": relative_path,
        "dicom_abs_path": str(dicom_path.resolve()),
        "exists": dicom_path.exists(),
        "file_size_bytes": dicom_path.stat().st_size if dicom_path.exists() else None,
        "read_ok": False,
        "error": None,
    }
    if not dicom_path.exists():
        row["error"] = "missing_file"
        return row

    try:
        ds = pydicom.dcmread(str(dicom_path), stop_before_pixels=True)
        number_of_frames = as_int(getattr(ds, "NumberOfFrames", None)) or 1
        row.update(
            {
                "read_ok": True,
                "manufacturer": getattr(ds, "Manufacturer", None),
                "manufacturer_model_name": getattr(ds, "ManufacturerModelName", None),
                "rows": as_int(getattr(ds, "Rows", None)),
                "columns": as_int(getattr(ds, "Columns", None)),
                "samples_per_pixel": as_int(getattr(ds, "SamplesPerPixel", None)),
                "bits_stored": as_int(getattr(ds, "BitsStored", None)),
                "photometric_interpretation": getattr(ds, "PhotometricInterpretation", None),
                "transfer_syntax_uid": str(getattr(ds.file_meta, "TransferSyntaxUID", "")),
                "sop_class_uid": str(getattr(ds, "SOPClassUID", "")),
                "number_of_frames": number_of_frames,
                "frame_time_ms": as_float(getattr(ds, "FrameTime", None)),
                "cine_rate": as_float(getattr(ds, "CineRate", None)),
                "recommended_display_frame_rate": as_float(getattr(ds, "RecommendedDisplayFrameRate", None)),
                "heart_rate": as_float(getattr(ds, "HeartRate", None)),
                "is_multiframe": number_of_frames > 1,
            }
        )
        row.update(first_ultrasound_region(ds))
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def build_summary(audit_df: pd.DataFrame) -> dict[str, Any]:
    clean = audit_df[audit_df["read_ok"].fillna(False)]
    multiframe = clean[clean["is_multiframe"].fillna(False)]
    still = clean[~clean["is_multiframe"].fillna(False)]
    return {
        "n_records": int(len(audit_df)),
        "n_read_ok": int(len(clean)),
        "n_read_failed": int(len(audit_df) - len(clean)),
        "n_multiframe": int(len(multiframe)),
        "n_still": int(len(still)),
        "n_studies": int(audit_df["study_id"].nunique()),
        "n_subjects": int(audit_df["subject_id"].nunique()),
        "frame_count_min_multiframe": int(multiframe["number_of_frames"].min()) if not multiframe.empty else None,
        "frame_count_median_multiframe": float(multiframe["number_of_frames"].median()) if not multiframe.empty else None,
        "frame_count_max_multiframe": int(multiframe["number_of_frames"].max()) if not multiframe.empty else None,
        "top_manufacturers": clean["manufacturer"].fillna("unknown").value_counts().head(10).to_dict(),
    }


def main() -> int:
    args = parse_args()
    records_df = pd.read_csv(args.records_csv)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = [audit_one(record, args.data_root.resolve()) for _, record in records_df.iterrows()]
    audit_df = pd.DataFrame(rows)
    summary = build_summary(audit_df)

    audit_df.to_csv(output_dir / "dicom_audit.csv", index=False)
    multiframe_df = audit_df[audit_df["is_multiframe"].fillna(False)].copy()
    multiframe_df.to_csv(output_dir / "cine_candidates.csv", index=False)
    (output_dir / "audit_summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {output_dir / 'dicom_audit.csv'}")
    print(f"[written] {output_dir / 'cine_candidates.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
