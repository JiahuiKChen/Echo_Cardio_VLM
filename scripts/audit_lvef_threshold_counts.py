#!/usr/bin/env python3
"""Count exact LVEF threshold ties by split and prespecified cohort scope.

The audit reconstructs the historical exact-raw-name LVEF label before imaging
linkage, then intersects it with a supplied imaging-eligible study manifest. It
emits aggregate counts only. It does not open predictions, calculate metrics,
or fit a model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from audit_lvef_multitask_artifacts import (
    canonical_identifier_series,
    historical_selected_lvef_preimage,
)
from lvef_multitask_audit_utils import (
    SPLIT_COLUMNS,
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    load_table,
    resolve_column,
    run_guarded,
    write_aggregate_csv,
    write_json,
)


SCOPES = ("selected_preimaging", "primary_common_imaging_eligible")
SPLITS = ("all", "train", "val", "test")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected-studies", type=Path, required=True)
    parser.add_argument("--structured-measurements", type=Path, required=True)
    parser.add_argument("--imaging-eligible-studies", type=Path, required=True)
    parser.add_argument("--split-map", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=40.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def _subject_study(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    subject = resolve_column(frame, SUBJECT_COLUMNS, required=True, label=f"{label} subject")
    study = resolve_column(frame, STUDY_COLUMNS, required=True, label=f"{label} study")
    assert subject is not None and study is not None
    result = pd.DataFrame(
        {
            "subject_id": canonical_identifier_series(frame[subject]),
            "study_id": canonical_identifier_series(frame[study]),
        }
    )
    if result.isna().any().any():
        raise ValueError(f"{label} has missing identifiers")
    if result["study_id"].duplicated().any():
        raise ValueError(f"{label} has duplicate studies")
    if result.groupby("study_id")["subject_id"].nunique().gt(1).any():
        raise ValueError(f"{label} maps one study to multiple subjects")
    return result


def _split_authority(frame: pd.DataFrame) -> pd.DataFrame:
    subject = resolve_column(frame, SUBJECT_COLUMNS, required=True, label="split subject")
    split = resolve_column(frame, SPLIT_COLUMNS, required=True, label="split assignment")
    assert subject is not None and split is not None
    result = pd.DataFrame(
        {
            "subject_id": canonical_identifier_series(frame[subject]),
            "split": frame[split].astype("string").str.strip().str.casefold(),
        }
    )
    if result.isna().any().any() or result["subject_id"].duplicated().any():
        raise ValueError("Split map contains missing or duplicate subjects")
    if not set(result["split"]).issubset({"train", "val", "test"}):
        raise ValueError("Split map contains an invalid split")
    return result


def threshold_count_rows(
    selected: pd.DataFrame,
    structured: pd.DataFrame,
    imaging: pd.DataFrame,
    split_map: pd.DataFrame,
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    selected_pairs = _subject_study(selected, "selected cohort")
    imaging_pairs = _subject_study(imaging, "imaging-eligible cohort")
    if not set(imaging_pairs["study_id"]).issubset(set(selected_pairs["study_id"])):
        raise ValueError("Imaging-eligible cohort contains a nonselected study")
    selected_owners = selected_pairs.set_index("study_id")["subject_id"]
    imaging_owners = imaging_pairs.set_index("study_id")["subject_id"]
    if not imaging_owners.eq(selected_owners.reindex(imaging_owners.index)).all():
        raise ValueError("Imaging-eligible ownership differs from selected authority")

    labels = historical_selected_lvef_preimage(selected, structured)
    labels["subject_id"] = canonical_identifier_series(labels["subject_id"])
    labels["study_id"] = canonical_identifier_series(labels["study_id"])
    split_authority = _split_authority(split_map)
    if set(selected_pairs["subject_id"]) != set(split_authority["subject_id"]):
        raise ValueError("Split map is not an exact selected-subject authority")
    labels = labels.merge(
        split_authority, how="left", on="subject_id", validate="one_to_one"
    )
    if labels["split"].isna().any():
        raise ValueError("An LVEF label lacks a split assignment")

    scopes = {
        "selected_preimaging": labels,
        "primary_common_imaging_eligible": labels[
            labels["study_id"].isin(set(imaging_pairs["study_id"]))
        ].copy(),
    }
    rows: list[dict[str, Any]] = []
    for scope in SCOPES:
        scoped = scopes[scope]
        for split in SPLITS:
            subset = scoped if split == "all" else scoped[scoped["split"] == split]
            equal = subset["lvef"].eq(float(threshold))
            rows.append(
                {
                    "cohort_scope": scope,
                    "split": split,
                    "threshold": float(threshold),
                    "comparison": "EXACT_NUMERIC_EQUALITY_AFTER_HISTORICAL_MEDIAN",
                    "n_observed_lvef": int(len(subset)),
                    "n_labels_exactly_equal_threshold": int(equal.sum()),
                }
            )
    return rows


def main() -> int:
    args = parse_args()
    if not all(
        path.is_file()
        for path in (
            args.selected_studies,
            args.structured_measurements,
            args.imaging_eligible_studies,
            args.split_map,
        )
    ):
        raise FileNotFoundError("A required threshold-count input is unavailable")
    rows = threshold_count_rows(
        load_table(args.selected_studies),
        load_table(args.structured_measurements),
        load_table(args.imaging_eligible_studies),
        load_table(args.split_map),
        threshold=args.threshold,
    )
    output = args.output_dir.expanduser()
    write_aggregate_csv(
        pd.DataFrame(rows), output / "lvef_exact_threshold_counts.csv"
    )
    summary = {
        "audit": "lvef_exact_threshold_counts",
        "status": "PASS",
        "threshold": float(args.threshold),
        "n_cohort_scopes": len(SCOPES),
        "n_split_scopes": len(SPLITS),
        "n_aggregate_rows": len(rows),
        "exact_equality_rule": "PARSED_NUMERIC_EQUALS_THRESHOLD_AFTER_HISTORICAL_MEDIAN",
        "identifier_values_emitted": False,
        "predictions_read": False,
        "model_fitting_performed": False,
        "performance_computed": False,
    }
    write_json(summary, output / "lvef_exact_threshold_counts.summary.json")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
