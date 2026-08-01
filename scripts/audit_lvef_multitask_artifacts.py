#!/usr/bin/env python3
"""Inventory LVEF/multitask artifacts and emit aggregate denominator summaries.

Missing optional SCC inputs are reported as MISSING rather than raising a stack
trace. Identifier-level discrepancies are never written to the aggregate output.
Use the companion overlap and split audits with --restricted-output-dir for
identifier-level review on SCC.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import (
    SPLIT_COLUMNS,
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    entity_counts,
    load_table,
    require_restricted_path,
    resolve_column,
    write_aggregate_csv,
    write_json,
)


ARTIFACT_ARGUMENTS = {
    "public_dicom_records": "public-records",
    "eligible_studies": "eligible-studies",
    "selected_studies": "selected-studies",
    "downloaded_studies": "downloaded-studies",
    "readable_dicoms": "readable-dicoms",
    "extracted_clips": "extracted-clips",
    "clip_embeddings": "clip-embeddings",
    "study_embeddings": "study-embeddings",
    "structured_measurements": "structured-measurements",
    "lvef_labels": "lvef-labels",
    "split_map": "split-map",
    "multitask_panel": "multitask-panel",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    for destination, flag in ARTIFACT_ARGUMENTS.items():
        parser.add_argument(f"--{flag}", dest=destination, type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path)
    parser.add_argument("--task-prefix", default="task__")
    return parser.parse_args()


def inspect_artifact(name: str, path: Path | None) -> tuple[dict[str, Any], pd.DataFrame | None]:
    if path is None:
        return {"artifact": name, "status": "NOT_SUPPLIED", "n_rows": None, "n_studies": None, "n_subjects": None}, None
    path = path.expanduser()
    if not path.exists():
        return {"artifact": name, "status": "MISSING", "n_rows": None, "n_studies": None, "n_subjects": None}, None
    try:
        frame = load_table(path)
        counts = entity_counts(frame)
        return {"artifact": name, "status": "OK", **counts}, frame
    except Exception as exc:  # audit inventory must fail gracefully per artifact
        return {
            "artifact": name,
            "status": "ERROR",
            "n_rows": None,
            "n_studies": None,
            "n_subjects": None,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }, None


def numeric_lvef_subset(frame: pd.DataFrame) -> pd.DataFrame:
    measurement_col = resolve_column(frame, ("measurement", "measurement_name", "canonical_measurement"))
    if measurement_col:
        frame = frame[frame[measurement_col].astype(str).str.strip().str.lower().eq("lvef")].copy()
    value_col = resolve_column(frame, ("lvef", "lvef_value", "target", "result_numeric", "result", "value"))
    if value_col:
        frame = frame[pd.to_numeric(frame[value_col], errors="coerce").notna()].copy()
    return frame


def split_count_rows(name: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    split_col = resolve_column(frame, SPLIT_COLUMNS)
    if split_col is None:
        return []
    subject_col = resolve_column(frame, SUBJECT_COLUMNS)
    study_col = resolve_column(frame, STUDY_COLUMNS)
    rows: list[dict[str, Any]] = []
    for split_name, group in frame.groupby(split_col, dropna=False):
        rows.append(
            {
                "artifact": name,
                "split": str(split_name),
                "n_rows": int(len(group)),
                "n_studies": int(group[study_col].nunique()) if study_col else None,
                "n_subjects": int(group[subject_col].nunique()) if subject_col else None,
            }
        )
    return rows


def task_denominator_rows(frame: pd.DataFrame, prefix: str) -> list[dict[str, Any]]:
    task_columns = [column for column in frame.columns if str(column).startswith(prefix)]
    split_col = resolve_column(frame, SPLIT_COLUMNS)
    rows: list[dict[str, Any]] = []
    split_groups = [("all", frame)] if split_col is None else list(frame.groupby(split_col, dropna=False))
    for task in task_columns:
        numeric = pd.to_numeric(frame[task], errors="coerce")
        for split_name, group in split_groups:
            mask = numeric.loc[group.index].notna()
            rows.append({"task": str(task), "split": str(split_name), "n_with_target": int(mask.sum())})
    return rows


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    inventory_rows: list[dict[str, Any]] = []
    restricted_warning_rows: list[dict[str, str]] = []
    frames: dict[str, pd.DataFrame] = {}
    for name in ARTIFACT_ARGUMENTS:
        row, frame = inspect_artifact(name, getattr(args, name))
        inventory_rows.append(row)
        if row["status"] in {"MISSING", "ERROR"}:
            supplied_path = getattr(args, name)
            restricted_warning_rows.append(
                {
                    "artifact": name,
                    "status": str(row["status"]),
                    "supplied_path": str(supplied_path) if supplied_path is not None else "",
                    "error_type": str(row.get("error_type", "")),
                    "error_message": str(row.get("error_message", "")),
                }
            )
        if frame is not None:
            frames[name] = frame

    denominator_rows: list[dict[str, Any]] = []
    for row in inventory_rows:
        denominator_rows.append(
            {
                "stage": row["artifact"],
                "status": row["status"],
                "n_rows": row.get("n_rows"),
                "n_studies": row.get("n_studies"),
                "n_subjects": row.get("n_subjects"),
            }
        )

    if "structured_measurements" in frames:
        lvef_preimage = numeric_lvef_subset(frames["structured_measurements"])
        counts = entity_counts(lvef_preimage)
        denominator_rows.append({"stage": "numeric_lvef_before_imaging", "status": "DERIVED", **counts})

        if "study_embeddings" in frames:
            label_study_col = resolve_column(lvef_preimage, STUDY_COLUMNS)
            embedding_study_col = resolve_column(frames["study_embeddings"], STUDY_COLUMNS)
            if label_study_col and embedding_study_col:
                embedded_ids = set(frames["study_embeddings"][embedding_study_col].dropna().astype(str))
                linked = lvef_preimage[lvef_preimage[label_study_col].astype(str).isin(embedded_ids)]
                counts = entity_counts(linked)
                denominator_rows.append({"stage": "numeric_lvef_plus_embedding", "status": "DERIVED", **counts})

    split_rows: list[dict[str, Any]] = []
    for name, frame in frames.items():
        split_rows.extend(split_count_rows(name, frame))

    task_rows = task_denominator_rows(frames["multitask_panel"], args.task_prefix) if "multitask_panel" in frames else []

    inventory_df = pd.DataFrame(inventory_rows)
    safe_inventory_columns = [column for column in inventory_df.columns if column not in {"error_message"}]
    write_aggregate_csv(inventory_df[safe_inventory_columns], args.output_dir / "artifact_inventory.csv")
    write_aggregate_csv(pd.DataFrame(denominator_rows), args.output_dir / "denominator_summary.csv")
    write_aggregate_csv(
        pd.DataFrame(split_rows, columns=["artifact", "split", "n_rows", "n_studies", "n_subjects"]),
        args.output_dir / "split_counts.csv",
    )
    write_aggregate_csv(
        pd.DataFrame(task_rows, columns=["task", "split", "n_with_target"]),
        args.output_dir / "multitask_task_denominators.csv",
    )

    status_counts = inventory_df["status"].value_counts().to_dict()
    restricted_written = False
    if args.restricted_output_dir is not None:
        restricted_dir = require_restricted_path(args.restricted_output_dir)
        pd.DataFrame(
            restricted_warning_rows,
            columns=["artifact", "status", "supplied_path", "error_type", "error_message"],
        ).to_csv(restricted_dir / "artifact_warnings_restricted.csv", index=False)
        restricted_written = True
    summary = {
        "audit": "lvef_multitask_artifacts",
        "status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "n_split_rows": len(split_rows),
        "n_task_denominator_rows": len(task_rows),
        "patient_level_output_written": False,
        "restricted_warnings_written": restricted_written,
        "notes": [
            "NOT_SUPPLIED and MISSING artifacts are expected during local development.",
            "Use restricted SCC paths for identifier-level overlap and split warnings.",
            "No model fitting or test-performance computation is performed.",
        ],
    }
    write_json(summary, args.output_dir / "audit_summary.json")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
