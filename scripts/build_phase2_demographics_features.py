#!/usr/bin/env python3
"""Build restricted Phase 2 demographics features for non-image baselines.

This script is intended to run on SCC. It queries approved MIMIC-IV hospital
demographics through BigQuery, joins them to the Phase 2 selected-study
denominator, and writes a restricted derived row-level feature CSV under the
Phase 2 restricted output root.

The output is for local/SCC modeling only. It must not be committed, uploaded,
pasted into chat, or copied outside approved secure storage.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_FULLSCALE_ROOT = Path("outputs/cloud_cohorts/fullscale_all")
DEFAULT_SELECTED_STUDIES = DEFAULT_FULLSCALE_ROOT / "manifests" / "all_eligible_studies.csv"
DEFAULT_BQ_DATA_PROJECT = "physionet-data"
DEFAULT_HOSP_DATASET = "mimiciv_3_1_hosp"
DATE_CANDIDATES = [
    "study_datetime",
    "first_acquisition_datetime",
    "measurement_datetime",
    "note_charttime",
    "last_acquisition_datetime",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=None, help="Restricted Phase 2 output root.")
    parser.add_argument("--output-csv", type=Path, default=None, help="Restricted row-level demographics feature CSV.")
    parser.add_argument("--selected-studies-csv", type=Path, default=DEFAULT_SELECTED_STUDIES)
    parser.add_argument("--billing-project", default=os.environ.get("ECHO_AI_BILLING_PROJECT", "mimic-iv-anesthesia"))
    parser.add_argument("--bq-data-project", default=DEFAULT_BQ_DATA_PROJECT)
    parser.add_argument("--hosp-dataset", default=DEFAULT_HOSP_DATASET)
    parser.add_argument("--include-race", action="store_true", help="Add most-frequent admission race per subject.")
    parser.add_argument(
        "--allow-restricted-derived-export",
        action="store_true",
        help="Required acknowledgment before writing restricted derived row-level demographics features.",
    )
    parser.add_argument(
        "--allow-repo-output-for-testing",
        action="store_true",
        help="Permit output inside the git worktree for synthetic tests only.",
    )
    return parser.parse_args()


def inside_current_worktree(path: Path) -> bool:
    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    return resolved == cwd or cwd in resolved.parents


def resolve_output_csv(args: argparse.Namespace) -> Path:
    if args.output_csv is not None:
        out = args.output_csv
    elif args.output_root is not None:
        out = args.output_root / "nonimage_baselines" / "phase2_demographics_features_restricted.csv"
    else:
        out = Path("outputs") / "phase2_demographics_features_restricted.csv"
    if inside_current_worktree(out) and not args.allow_repo_output_for_testing:
        raise RuntimeError(
            f"Refusing to write restricted derived demographics inside the git worktree: {out}. "
            "Use a restricted SCC output directory."
        )
    return out


def normalize_time(value: Any) -> pd.Timestamp | pd.NaT:
    if pd.isna(value) or str(value).strip() == "":
        return pd.NaT
    return pd.to_datetime(value, errors="coerce", utc=True)


def selected_study_years(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing selected studies CSV: {path}")
    df = pd.read_csv(path)
    required = {"subject_id", "study_id"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Selected studies CSV missing required columns: {sorted(missing)}")

    out = df[["subject_id", "study_id"]].copy()
    out["subject_id"] = pd.to_numeric(out["subject_id"], errors="coerce").astype("Int64")
    out["study_id"] = pd.to_numeric(out["study_id"], errors="coerce").astype("Int64")
    out = out.dropna(subset=["subject_id", "study_id"]).copy()
    out["subject_id"] = out["subject_id"].astype(int)
    out["study_id"] = out["study_id"].astype(int)

    timestamps = []
    basis = []
    for _, row in df.loc[out.index].iterrows():
        chosen = pd.NaT
        chosen_col = ""
        for col in DATE_CANDIDATES:
            if col in df.columns:
                candidate = normalize_time(row.get(col))
                if pd.notna(candidate):
                    chosen = candidate
                    chosen_col = col
                    break
        timestamps.append(chosen)
        basis.append(chosen_col if chosen_col else "anchor_age_only")
    out["study_year"] = [int(ts.year) if pd.notna(ts) else np.nan for ts in timestamps]
    out["age_basis"] = ["anchor_age_plus_" + b if b != "anchor_age_only" else b for b in basis]
    return out.drop_duplicates(subset=["subject_id", "study_id"]).reset_index(drop=True)


def subject_list_sql(subjects: list[int]) -> str:
    if not subjects:
        raise ValueError("No subject IDs available for demographics query.")
    return ", ".join(str(int(subject)) for subject in subjects)


def run_bq_csv(sql: str, billing_project: str) -> pd.DataFrame:
    cmd = [
        "bq",
        f"--project_id={billing_project}",
        "query",
        "--nouse_legacy_sql",
        "--format=csv",
        "--max_rows=10000000",
        sql,
    ]
    proc = subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"BigQuery command failed:\n{proc.stderr.strip()}")
    if not proc.stdout.strip():
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(proc.stdout))


def query_patients(subjects: list[int], args: argparse.Namespace) -> pd.DataFrame:
    subject_sql = subject_list_sql(subjects)
    table = f"`{args.bq_data_project}.{args.hosp_dataset}.patients`"
    sql = f"""
SELECT
  subject_id,
  gender AS sex,
  anchor_age,
  anchor_year
FROM {table}
WHERE subject_id IN UNNEST([{subject_sql}])
"""
    return run_bq_csv(sql, args.billing_project)


def query_race(subjects: list[int], args: argparse.Namespace) -> pd.DataFrame:
    subject_sql = subject_list_sql(subjects)
    table = f"`{args.bq_data_project}.{args.hosp_dataset}.admissions`"
    sql = f"""
WITH race_counts AS (
  SELECT
    subject_id,
    race,
    COUNT(*) AS n_admissions
  FROM {table}
  WHERE subject_id IN UNNEST([{subject_sql}])
    AND race IS NOT NULL
    AND TRIM(CAST(race AS STRING)) != ''
  GROUP BY subject_id, race
)
SELECT subject_id, race
FROM race_counts
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY subject_id
  ORDER BY n_admissions DESC, race
) = 1
"""
    return run_bq_csv(sql, args.billing_project)


def build_features(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    selected = selected_study_years(args.selected_studies_csv)
    subjects = sorted(selected["subject_id"].drop_duplicates().astype(int).tolist())
    patients = query_patients(subjects, args)
    if patients.empty:
        raise RuntimeError("Patient demographics query returned zero rows.")
    patients["subject_id"] = pd.to_numeric(patients["subject_id"], errors="coerce").astype("Int64")
    patients = patients.dropna(subset=["subject_id"]).copy()
    patients["subject_id"] = patients["subject_id"].astype(int)

    merged = selected.merge(patients, on="subject_id", how="left", validate="many_to_one")
    merged["anchor_age"] = pd.to_numeric(merged["anchor_age"], errors="coerce")
    merged["anchor_year"] = pd.to_numeric(merged["anchor_year"], errors="coerce")
    merged["study_year"] = pd.to_numeric(merged["study_year"], errors="coerce")
    merged["age_at_echo_approx"] = merged["anchor_age"]
    has_year = merged["study_year"].notna() & merged["anchor_year"].notna()
    merged.loc[has_year, "age_at_echo_approx"] = (
        merged.loc[has_year, "anchor_age"] + (merged.loc[has_year, "study_year"] - merged.loc[has_year, "anchor_year"])
    )
    merged["age_at_echo_approx"] = merged["age_at_echo_approx"].clip(lower=0)
    merged["sex"] = merged["sex"].astype("string").fillna("Unknown").str.strip().replace({"": "Unknown"})

    features = merged[["subject_id", "study_id", "age_at_echo_approx", "sex", "age_basis"]].copy()
    features["race_included"] = False
    race_source = "not_requested"

    if args.include_race:
        race = query_race(subjects, args)
        if race.empty:
            features["race"] = "Unknown"
            race_source = "requested_but_no_admissions_race_rows"
        else:
            race["subject_id"] = pd.to_numeric(race["subject_id"], errors="coerce").astype("Int64")
            race = race.dropna(subset=["subject_id"]).copy()
            race["subject_id"] = race["subject_id"].astype(int)
            features = features.merge(race[["subject_id", "race"]].drop_duplicates("subject_id"), on="subject_id", how="left")
            features["race"] = features["race"].astype("string").fillna("Unknown").str.strip().replace({"": "Unknown"})
            race_source = "most_frequent_admission_race_by_subject"
        features["race_included"] = True

    summary = {
        "selected_studies_csv": str(args.selected_studies_csv),
        "n_rows": int(len(features)),
        "n_subjects": int(features["subject_id"].nunique()),
        "n_studies": int(features["study_id"].nunique()),
        "features": ["age_at_echo_approx", "sex"] + (["race"] if args.include_race else []),
        "age_feature": "age_at_echo_approx",
        "age_is_approximate": True,
        "age_basis_counts": features["age_basis"].value_counts(dropna=False).to_dict(),
        "sex_missing_or_unknown": int(features["sex"].fillna("Unknown").eq("Unknown").sum()),
        "race_included": bool(args.include_race),
        "race_source": race_source,
        "identifier_columns_in_restricted_output": ["subject_id", "study_id"],
        "restriction_notice": (
            "This is restricted derived row-level demographics data for SCC modeling only. "
            "Do not commit, upload, paste, or copy outside approved secure storage."
        ),
    }
    if args.include_race and "race" in features:
        summary["race_missing_or_unknown"] = int(features["race"].fillna("Unknown").eq("Unknown").sum())

    return features, summary


def main() -> int:
    args = parse_args()
    if not args.allow_restricted_derived_export:
        raise RuntimeError("--allow-restricted-derived-export is required to write restricted demographics features.")
    output_csv = resolve_output_csv(args)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    features, summary = build_features(args)
    features.to_csv(output_csv, index=False)
    summary_path = output_csv.with_suffix(".summary.json")
    summary.update(
        {
            "output_csv": str(output_csv),
            "output_summary_json": str(summary_path),
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "restricted_row_level_demographics_written": True,
            "patient_level_predictions_written": False,
            "row_level_model_outputs_written": False,
            "aggregate_only": False,
        }
    )
    summary_path.write_text(json.dumps(summary, indent=2))

    print(
        json.dumps(
            {
                "output_csv": str(output_csv),
                "summary_json": str(summary_path),
                "n_rows": summary["n_rows"],
                "n_subjects": summary["n_subjects"],
                "features": summary["features"],
                "age_is_approximate": summary["age_is_approximate"],
                "race_included": summary["race_included"],
                "restricted_derived_row_level_data": True,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
