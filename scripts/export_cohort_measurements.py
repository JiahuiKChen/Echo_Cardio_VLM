#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export structured_measurement rows for selected MIMIC-IV-ECHO studies via bq CLI."
    )
    parser.add_argument(
        "--selected-studies-csv",
        type=Path,
        required=True,
        help="CSV containing at least subject_id, study_id, measurement_id columns.",
    )
    parser.add_argument(
        "--billing-project",
        required=True,
        help="GCP project for BigQuery query jobs.",
    )
    parser.add_argument("--bq-project", default="physionet-data", help="BigQuery data project.")
    parser.add_argument("--bq-dataset", default="mimiciv_echo", help="BigQuery dataset.")
    parser.add_argument("--output-csv", type=Path, required=True, help="Destination CSV path.")
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=None,
        help="Optional summary JSON path. Defaults to <output-csv>.summary.json",
    )
    parser.add_argument("--query-sql-out", type=Path, default=None, help="Optional path to save generated SQL text.")
    parser.add_argument("--max-rows", type=int, default=200000000, help="bq --max_rows limit.")
    return parser.parse_args()


def build_selected_structs(studies_df: pd.DataFrame) -> list[tuple[int, int, int]]:
    required = {"subject_id", "study_id", "measurement_id"}
    missing = required - set(studies_df.columns)
    if missing:
        raise ValueError(f"Missing required columns in selected studies CSV: {sorted(missing)}")

    clean = studies_df.dropna(subset=["measurement_id"]).copy()
    clean["subject_id"] = clean["subject_id"].astype(int)
    clean["study_id"] = clean["study_id"].astype(int)
    clean["measurement_id"] = clean["measurement_id"].astype(int)
    clean = clean.drop_duplicates(subset=["subject_id", "study_id", "measurement_id"]).reset_index(drop=True)
    return list(clean[["subject_id", "study_id", "measurement_id"]].itertuples(index=False, name=None))


def build_query(structs: list[tuple[int, int, int]], bq_project: str, bq_dataset: str) -> str:
    struct_sql = ",\n    ".join(
        [
            f"STRUCT({sid} AS subject_id, {stid} AS study_id, {mid} AS measurement_id)"
            for sid, stid, mid in structs
        ]
    )
    return f"""
WITH selected AS (
  SELECT *
  FROM UNNEST([
    {struct_sql}
  ])
)
SELECT
  s.subject_id,
  s.study_id,
  s.measurement_id,
  m.measurement_datetime,
  m.test_type,
  m.measurement,
  m.measurement_description,
  m.result,
  m.unit
FROM selected s
LEFT JOIN `{bq_project}.{bq_dataset}.structured_measurement` m
  ON m.subject_id = s.subject_id
 AND m.measurement_id = s.measurement_id
ORDER BY s.study_id, m.measurement_datetime, m.test_type, m.measurement
""".strip()


def run_bq_query(query: str, billing_project: str, max_rows: int) -> str:
    cmd = [
        "bq",
        f"--project_id={billing_project}",
        "query",
        "--nouse_legacy_sql",
        "--format=csv",
        f"--max_rows={max_rows}",
        query,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"bq query failed (exit {proc.returncode}):\n{proc.stderr.strip()}")
    return proc.stdout


def write_empty_output(path: Path) -> None:
    columns = [
        "subject_id",
        "study_id",
        "measurement_id",
        "measurement_datetime",
        "test_type",
        "measurement",
        "measurement_description",
        "result",
        "unit",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=columns).to_csv(path, index=False)


def build_summary(
    selected_structs: list[tuple[int, int, int]],
    output_df: pd.DataFrame,
    args: argparse.Namespace,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "billing_project": args.billing_project,
        "bq_dataset": f"{args.bq_project}.{args.bq_dataset}",
        "n_selected_studies": int(pd.DataFrame(selected_structs, columns=["subject_id", "study_id", "measurement_id"])["study_id"].nunique())
        if selected_structs
        else 0,
        "n_selected_measurement_ids": int(len(selected_structs)),
        "n_rows_exported": int(len(output_df)),
        "n_rows_with_result": int(output_df["result"].notna().sum()) if "result" in output_df else 0,
        "n_unique_measurements": int(output_df["measurement"].nunique()) if "measurement" in output_df else 0,
        "output_csv": str(args.output_csv.resolve()),
    }
    if not output_df.empty and "test_type" in output_df:
        summary["top_test_types"] = output_df["test_type"].fillna("unknown").value_counts().head(10).to_dict()
    return summary


def main() -> int:
    args = parse_args()
    studies_df = pd.read_csv(args.selected_studies_csv)
    selected_structs = build_selected_structs(studies_df)

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    if not selected_structs:
        write_empty_output(args.output_csv)
        output_df = pd.read_csv(args.output_csv)
        summary = build_summary(selected_structs, output_df, args)
        summary_path = args.summary_json or args.output_csv.with_suffix(".summary.json")
        summary_path.write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        print(f"[written] {args.output_csv.resolve()}")
        print(f"[written] {summary_path.resolve()}")
        return 0

    query = build_query(selected_structs, bq_project=args.bq_project, bq_dataset=args.bq_dataset)
    if args.query_sql_out:
        args.query_sql_out.parent.mkdir(parents=True, exist_ok=True)
        args.query_sql_out.write_text(query)

    csv_text = run_bq_query(query=query, billing_project=args.billing_project, max_rows=args.max_rows)
    args.output_csv.write_text(csv_text)

    output_df = pd.read_csv(args.output_csv)
    summary = build_summary(selected_structs, output_df, args)
    summary_path = args.summary_json or args.output_csv.with_suffix(".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"[written] {args.output_csv.resolve()}")
    print(f"[written] {summary_path.resolve()}")
    if args.query_sql_out:
        print(f"[written] {args.query_sql_out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
