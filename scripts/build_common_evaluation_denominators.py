#!/usr/bin/env python3
"""Dry-run common denominators across vision, structured, and fusion inputs.

The script computes set identity and intersections only. It never fits a model or
computes test performance. Identifier-level common sets may be written only to an
explicit restricted directory outside the repository.
"""
from __future__ import annotations

import argparse
import json
from functools import reduce
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import (
    SPLIT_COLUMNS,
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    id_set_hash,
    load_table,
    normalized_ids,
    parse_named_path,
    require_restricted_path,
    resolve_column,
    write_aggregate_csv,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modality", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--target-column", action="append", default=[])
    parser.add_argument("--task-prefix", default="task__")
    parser.add_argument("--label-column", default=None, help="Optional common label column for equality checking.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path)
    return parser.parse_args()


def target_names(frames: dict[str, pd.DataFrame], requested: list[str], prefix: str) -> list[str]:
    if requested:
        return list(dict.fromkeys(requested))
    long_task_sets: list[set[str]] = []
    for frame in frames.values():
        task_col = resolve_column(frame, ("task_col", "task", "target_name"))
        if task_col is None:
            long_task_sets = []
            break
        long_task_sets.append(set(frame[task_col].dropna().astype(str).tolist()))
    if long_task_sets:
        return sorted(set.intersection(*long_task_sets))
    common_columns = reduce(set.intersection, (set(frame.columns) for frame in frames.values()))
    tasks = sorted(column for column in common_columns if str(column).startswith(prefix))
    return tasks or ["__all__"]


def ids_for_target(frame: pd.DataFrame, id_col: str, target: str) -> set[str]:
    if target == "__all__":
        return normalized_ids(frame[id_col])
    if target not in frame.columns:
        task_col = resolve_column(frame, ("task_col", "task", "target_name"))
        if task_col is None:
            return set()
        task_values = frame[task_col].astype(str)
        target_mask = task_values.eq(target)
        value_col = resolve_column(frame, ("y_true", "target_value", "label_value", "result"))
        if value_col:
            target_mask &= pd.to_numeric(frame[value_col], errors="coerce").notna()
        return normalized_ids(frame.loc[target_mask, id_col])
    mask = pd.to_numeric(frame[target], errors="coerce").notna()
    return normalized_ids(frame.loc[mask, id_col])


def compute_common_ids(
    modality_frames: dict[str, pd.DataFrame],
    *,
    id_candidates: tuple[str, ...] = STUDY_COLUMNS,
    target: str = "__all__",
) -> tuple[dict[str, set[str]], set[str]]:
    sets: dict[str, set[str]] = {}
    for name, frame in modality_frames.items():
        id_col = resolve_column(frame, id_candidates, required=True, label=f"identifier for {name}")
        assert id_col is not None
        sets[name] = ids_for_target(frame, id_col, target)
    intersection = set.intersection(*sets.values()) if sets else set()
    return sets, intersection


def labels_equal_on_common(
    frames: dict[str, pd.DataFrame],
    common_ids: set[str],
    label_column: str,
    id_candidates: tuple[str, ...],
    target: str = "__all__",
) -> tuple[bool, int]:
    merged: pd.DataFrame | None = None
    within_modality_mismatches = 0
    for name, frame in frames.items():
        id_col = resolve_column(frame, id_candidates, required=True)
        if label_column not in frame.columns:
            return False, len(common_ids)
        assert id_col is not None
        row_mask = frame[id_col].astype(str).isin(common_ids)
        if target != "__all__":
            task_col = resolve_column(frame, ("task_col", "task", "target_name"))
            if task_col is not None:
                row_mask &= frame[task_col].astype(str).eq(target)
        part = frame.loc[row_mask, [id_col, label_column]].copy()
        part[id_col] = part[id_col].astype(str)
        part[label_column] = pd.to_numeric(part[label_column], errors="coerce")
        within_modality_mismatches += int((part.groupby(id_col)[label_column].nunique(dropna=False) > 1).sum())
        part = part.drop_duplicates(id_col).rename(columns={id_col: "_id", label_column: f"label__{name}"})
        merged = part if merged is None else merged.merge(part, on="_id", how="outer")
    if merged is None or merged.empty:
        return within_modality_mismatches == 0, within_modality_mismatches
    label_cols = [column for column in merged.columns if column.startswith("label__")]
    mismatch = merged[label_cols].nunique(axis=1, dropna=False) > 1
    mismatch_count = int(mismatch.sum()) + within_modality_mismatches
    return mismatch_count == 0, mismatch_count


def main() -> int:
    args = parse_args()
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for raw in args.modality:
        name, path = parse_named_path(raw)
        if not path.exists():
            missing.append(f"{name}={path}")
            continue
        frames[name] = load_table(path)
    if missing or len(frames) < 2:
        print(json.dumps({"status": "BLOCKED_MISSING_INPUT", "missing": missing, "n_modalities_loaded": len(frames)}, indent=2))
        return 2

    targets = target_names(frames, args.target_column, args.task_prefix)
    aggregate_rows: list[dict[str, Any]] = []
    restricted_rows: list[dict[str, str]] = []
    identity_failures = 0
    for target in targets:
        for identifier_level, candidates in (("study", STUDY_COLUMNS), ("subject", SUBJECT_COLUMNS)):
            try:
                sets, common = compute_common_ids(frames, id_candidates=candidates, target=target)
            except ValueError:
                if identifier_level == "subject":
                    continue
                raise
            all_identical = all(values == next(iter(sets.values())) for values in sets.values())
            if not all_identical:
                identity_failures += 1
            row: dict[str, Any] = {
                "target": target,
                "identifier_level": identifier_level,
                "sets_identical": all_identical,
                "n_common": len(common),
                "common_id_set_sha256": id_set_hash(common),
            }
            for name, values in sorted(sets.items()):
                row[f"n_{name}"] = len(values)
                row[f"n_{name}_outside_common"] = len(values - common)
            if args.label_column and identifier_level == "study":
                equal, mismatch_count = labels_equal_on_common(
                    frames, common, args.label_column, candidates, target=target
                )
                row["labels_identical_on_common"] = equal
                row["n_label_mismatches"] = mismatch_count
                if not equal:
                    identity_failures += 1
            aggregate_rows.append(row)
            if args.restricted_output_dir is not None:
                for name, values in sets.items():
                    for identifier in sorted(values - common):
                        restricted_rows.append(
                            {
                                "target": target,
                                "identifier_level": identifier_level,
                                "modality": name,
                                "identifier": identifier,
                                "status": "OUTSIDE_COMMON_INTERSECTION",
                            }
                        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_aggregate_csv(pd.DataFrame(aggregate_rows), args.output_dir / "common_denominator_audit.csv")
    restricted_written = False
    if args.restricted_output_dir is not None:
        restricted_dir = require_restricted_path(args.restricted_output_dir)
        pd.DataFrame(
            restricted_rows,
            columns=["target", "identifier_level", "modality", "identifier", "status"],
        ).to_csv(restricted_dir / "common_denominator_discrepancies_restricted.csv", index=False)
        restricted_written = True

    summary = {
        "audit": "common_evaluation_denominators",
        "modalities": sorted(frames),
        "n_targets": len(targets),
        "n_identity_failures": identity_failures,
        "restricted_discrepancies_written": restricted_written,
        "model_fitting_performed": False,
        "test_performance_computed": False,
    }
    write_json(summary, args.output_dir / "common_denominator_audit.summary.json")
    print(json.dumps(summary, indent=2))
    return 1 if identity_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
