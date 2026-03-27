#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)


def resolve_record_list_path(args: argparse.Namespace) -> Path:
    if args.record_list is not None:
        return args.record_list.resolve()
    summary = load_json(args.summary_json.resolve())
    path = summary.get("resolved_paths", {}).get("record_list")
    if not path:
        raise FileNotFoundError("Could not resolve record list path from summary JSON.")
    return Path(path).resolve()


def resolve_metadata_names(summary_json: Path) -> dict[str, str]:
    summary = load_json(summary_json.resolve())
    resolved = summary.get("resolved_paths", {})
    out = {}
    for key in ["record_list", "study_list", "measurement_table"]:
        value = resolved.get(key)
        if value:
            out[key] = Path(value).name
    return out


def first_existing(columns: list[str], df: pd.DataFrame) -> str | None:
    for column in columns:
        if column in df.columns:
            return column
    return None


def infer_test_type_column(df: pd.DataFrame) -> str | None:
    candidates = [col for col in df.columns if "test_type" in col.lower()]
    return candidates[0] if candidates else None


def filter_candidates(df: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    stats: dict[str, Any] = {"initial_rows": int(len(df))}
    out = df.copy()

    if args.require_measurement_link and "has_measurement_link" in out.columns:
        out = out[out["has_measurement_link"].fillna(False)]
    stats["after_measurement_filter"] = int(len(out))

    if args.require_note_link and "has_note_link" in out.columns:
        out = out[out["has_note_link"].fillna(False)]
    stats["after_note_filter"] = int(len(out))

    if "n_dicoms" not in out.columns:
        raise KeyError("study manifest must contain n_dicoms")
    out = out[(out["n_dicoms"] >= args.min_dicoms) & (out["n_dicoms"] <= args.max_dicoms)]
    stats["after_dicom_bounds"] = int(len(out))

    test_type_column = infer_test_type_column(out)
    stats["test_type_column"] = test_type_column
    if args.prefer_test_type and test_type_column is not None:
        mask = out[test_type_column].astype(str).str.contains(args.prefer_test_type, case=False, na=False)
        filtered = out[mask]
        if not filtered.empty:
            out = filtered
    stats["after_test_type_filter"] = int(len(out))

    if out.empty:
        raise RuntimeError("No candidate studies remain after filtering.")

    rng = np.random.default_rng(args.seed)
    target_n_dicoms = float(out["n_dicoms"].median())
    out = out.copy()
    out["_score_abs_dicoms"] = (out["n_dicoms"] - target_n_dicoms).abs()
    out["_rand"] = rng.random(len(out))
    out = out.sort_values(["_score_abs_dicoms", "_rand", "study_id"]).reset_index(drop=True)

    if args.max_studies_per_subject > 0 and "subject_id" in out.columns:
        out = out.groupby("subject_id", as_index=False, sort=False).head(args.max_studies_per_subject)
    stats["after_subject_cap"] = int(len(out))

    selected = out.head(args.n_studies).copy()
    if len(selected) < args.n_studies:
        raise RuntimeError(
            f"Requested {args.n_studies} studies but only {len(selected)} matched the current filters."
        )

    selected = selected.drop(columns=["_score_abs_dicoms", "_rand"], errors="ignore")
    stats["selected_rows"] = int(len(selected))
    stats["selected_n_dicoms_sum"] = int(selected["n_dicoms"].sum())
    stats["selected_n_dicoms_mean"] = float(selected["n_dicoms"].mean())
    stats["selected_n_dicoms_median"] = float(selected["n_dicoms"].median())
    return selected, stats


def build_url_lists(
    selected_studies: pd.DataFrame,
    record_df: pd.DataFrame,
    summary_json: Path,
    base_url: str,
) -> tuple[list[str], list[str], list[str]]:
    base_url = base_url.rstrip("/")
    selected_study_ids = set(selected_studies["study_id"].tolist())
    selected_records = record_df[record_df["study_id"].isin(selected_study_ids)].copy()
    if selected_records.empty:
        raise RuntimeError("No DICOM record rows matched selected studies.")

    selected_records["relative_path"] = (
        selected_records["dicom_filepath"].astype(str).str.replace(r"^/+", "", regex=True)
    )
    selected_records = selected_records.sort_values(["study_id", "relative_path"]).reset_index(drop=True)

    metadata_names = resolve_metadata_names(summary_json)
    metadata_relative = [
        metadata_names.get("record_list", "echo-record-list.csv"),
        metadata_names.get("study_list", "echo-study-list.csv"),
    ]
    measurement_name = metadata_names.get("measurement_table")
    if measurement_name:
        metadata_relative.append(measurement_name)
    metadata_relative.extend(["SHA256SUMS.txt", "LICENSE.txt"])

    metadata_relative = list(dict.fromkeys(metadata_relative))
    dicom_relative = selected_records["relative_path"].tolist()
    url_list = [f"{base_url}/{path}" for path in metadata_relative + dicom_relative]
    return metadata_relative, dicom_relative, url_list


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(f"{line}\n" for line in lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Select a reproducible MIMIC-IV-ECHO pilot subset.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs" / "mimic_echo_metadata" / "study_manifest.csv",
        help="Path to the study manifest produced by mimic_echo_metadata_inspect.py",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs" / "mimic_echo_metadata" / "summary.json",
        help="Path to the summary JSON produced by mimic_echo_metadata_inspect.py",
    )
    parser.add_argument("--record-list", type=Path, default=None, help="Optional explicit path to echo-record-list.csv")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs" / "mimic_echo_subset" / "stage_b_pilot",
        help="Directory to write subset manifests and download lists.",
    )
    parser.add_argument("--n-studies", type=int, default=50, help="Number of studies to select.")
    parser.add_argument("--seed", type=int, default=1337, help="Random seed used for tie-breaking.")
    parser.add_argument("--min-dicoms", type=int, default=40, help="Minimum number of DICOMs per study.")
    parser.add_argument("--max-dicoms", type=int, default=120, help="Maximum number of DICOMs per study.")
    parser.add_argument(
        "--prefer-test-type",
        default="TTE",
        help="Preferred linked measurement test_type. Ignored if no test_type column is present.",
    )
    parser.add_argument(
        "--max-studies-per-subject",
        type=int,
        default=1,
        help="Maximum number of selected studies per subject. Set 0 to disable.",
    )
    parser.add_argument(
        "--base-url",
        default="https://physionet.org/files/mimic-iv-echo/1.0",
        help="Base URL for building download links.",
    )
    parser.add_argument(
        "--require-note-link",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require a note link in echo-study-list.csv",
    )
    parser.add_argument(
        "--require-measurement-link",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require a structured measurement link in echo-study-list.csv",
    )
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    summary_json = args.summary_json.resolve()
    record_list_path = resolve_record_list_path(args)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    study_manifest = load_csv(manifest_path)
    record_df = load_csv(record_list_path)

    selected_studies, stats = filter_candidates(study_manifest, args)
    metadata_relative, dicom_relative, url_list = build_url_lists(
        selected_studies=selected_studies,
        record_df=record_df,
        summary_json=summary_json,
        base_url=args.base_url,
    )

    selected_study_ids = set(selected_studies["study_id"].tolist())
    selected_records = record_df[record_df["study_id"].isin(selected_study_ids)].copy()
    selected_records["relative_path"] = (
        selected_records["dicom_filepath"].astype(str).str.replace(r"^/+", "", regex=True)
    )

    note_cols = [col for col in ["study_id", "subject_id", "note_id", "note_seq", "note_charttime"] if col in selected_studies.columns]
    selected_notes = selected_studies[note_cols].dropna(subset=["note_id"]) if "note_id" in selected_studies.columns else pd.DataFrame()

    stats.update(
        {
            "manifest_path": str(manifest_path),
            "record_list_path": str(record_list_path),
            "n_metadata_files": len(metadata_relative),
            "n_dicoms": len(dicom_relative),
            "base_url": args.base_url,
        }
    )

    selected_studies.to_csv(output_dir / "selected_studies.csv", index=False)
    selected_records.to_csv(output_dir / "selected_records.csv", index=False)
    if not selected_notes.empty:
        selected_notes.to_csv(output_dir / "selected_note_ids.csv", index=False)
    write_lines(output_dir / "selected_study_ids.txt", [str(x) for x in selected_studies["study_id"].tolist()])
    write_lines(output_dir / "metadata_relative_paths.txt", metadata_relative)
    write_lines(output_dir / "dicom_relative_paths.txt", dicom_relative)
    write_lines(output_dir / "all_relative_paths.txt", metadata_relative + dicom_relative)
    write_lines(output_dir / "download_urls.txt", url_list)
    (output_dir / "selection_summary.json").write_text(json.dumps(stats, indent=2))

    print(json.dumps(stats, indent=2))
    print(f"[written] {output_dir / 'selected_studies.csv'}")
    print(f"[written] {output_dir / 'selected_records.csv'}")
    print(f"[written] {output_dir / 'download_urls.txt'}")
    if not selected_notes.empty:
        print(f"[written] {output_dir / 'selected_note_ids.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
