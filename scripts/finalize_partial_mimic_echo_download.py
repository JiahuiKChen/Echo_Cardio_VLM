#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare a selected MIMIC-IV-ECHO subset against local files and emit clean complete/incomplete manifests."
    )
    parser.add_argument("--selected-records", type=Path, required=True, help="Path to selected_records.csv")
    parser.add_argument("--selected-studies", type=Path, required=True, help="Path to selected_studies.csv")
    parser.add_argument("--download-root", type=Path, required=True, help="Root containing downloaded MIMIC-IV-ECHO files")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for completion reports")
    parser.add_argument(
        "--base-url",
        default="https://physionet.org/files/mimic-iv-echo/1.0",
        help="Base URL used to regenerate download URLs for incomplete studies.",
    )
    return parser.parse_args()


def build_expected_table(selected_records: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        selected_records.groupby(["subject_id", "study_id"], as_index=False)
        .agg(expected_dicoms=("dicom_filepath", "count"))
        .sort_values(["study_id", "subject_id"])
        .reset_index(drop=True)
    )
    return grouped


def count_downloaded_dicoms(download_root: Path, subject_id: int, study_id: int) -> int:
    study_dir = download_root / "files" / f"p{subject_id // 1000000:02d}" / f"p{subject_id}" / f"s{study_id}"
    if not study_dir.exists():
        return 0
    return sum(1 for _ in study_dir.glob("*.dcm"))


def main() -> int:
    args = parse_args()
    selected_records = pd.read_csv(args.selected_records)
    selected_studies = pd.read_csv(args.selected_studies)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    completion = build_expected_table(selected_records)
    completion["actual_dicoms"] = [
        count_downloaded_dicoms(args.download_root.resolve(), int(subject_id), int(study_id))
        for subject_id, study_id in zip(completion["subject_id"], completion["study_id"])
    ]
    completion["complete"] = completion["actual_dicoms"] >= completion["expected_dicoms"]

    complete_ids = set(completion.loc[completion["complete"], "study_id"].tolist())
    incomplete_ids = set(completion.loc[~completion["complete"], "study_id"].tolist())

    complete_studies = selected_studies[selected_studies["study_id"].isin(complete_ids)].copy()
    incomplete_studies = selected_studies[selected_studies["study_id"].isin(incomplete_ids)].copy()
    complete_records = selected_records[selected_records["study_id"].isin(complete_ids)].copy()
    incomplete_records = selected_records[selected_records["study_id"].isin(incomplete_ids)].copy()

    incomplete_relative_paths = (
        incomplete_records["dicom_filepath"].astype(str).str.replace(r"^/+", "", regex=True).tolist()
        if not incomplete_records.empty
        else []
    )
    resume_urls = [f"{args.base_url.rstrip('/')}/{path}" for path in incomplete_relative_paths]

    completion.to_csv(output_dir / "study_completion_report.csv", index=False)
    complete_studies.to_csv(output_dir / "complete_studies.csv", index=False)
    incomplete_studies.to_csv(output_dir / "incomplete_studies.csv", index=False)
    complete_records.to_csv(output_dir / "complete_records.csv", index=False)
    incomplete_records.to_csv(output_dir / "incomplete_records.csv", index=False)
    (output_dir / "resume_download_urls.txt").write_text("".join(f"{url}\n" for url in resume_urls))

    summary = {
        "selected_studies": int(selected_studies["study_id"].nunique()),
        "complete_studies": int(complete_studies["study_id"].nunique()),
        "incomplete_studies": int(incomplete_studies["study_id"].nunique()),
        "selected_dicoms": int(len(selected_records)),
        "downloaded_dicoms_in_complete_studies": int(len(complete_records)),
        "resume_url_count": int(len(resume_urls)),
        "download_root": str(args.download_root.resolve()),
    }
    (output_dir / "partial_download_summary.json").write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {output_dir / 'study_completion_report.csv'}")
    print(f"[written] {output_dir / 'complete_studies.csv'}")
    print(f"[written] {output_dir / 'resume_download_urls.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
