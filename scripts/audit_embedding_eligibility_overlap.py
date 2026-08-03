#!/usr/bin/env python3
"""Reconcile selected, eligible, and embedded studies without Git-safe IDs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from lvef_multitask_audit_utils import (
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    load_table,
    load_tables,
    normalized_ids,
    require_restricted_path,
    resolve_column,
    run_guarded,
    write_aggregate_csv,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected-studies", type=Path, required=True)
    parser.add_argument("--study-embeddings", type=Path, required=True)
    parser.add_argument("--eligible-all-studies", type=Path, action="append")
    parser.add_argument("--prior-stage-studies", type=Path, action="append")
    parser.add_argument("--downloaded-studies", type=Path, action="append")
    parser.add_argument("--readable-dicoms", type=Path, action="append")
    parser.add_argument("--cine-candidates", type=Path, action="append")
    parser.add_argument("--extracted-clips", type=Path, action="append")
    parser.add_argument("--clip-embeddings", type=Path, action="append")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path)
    parser.add_argument(
        "--stage-lineage-complete",
        action="store_true",
        help="Assert that every selected-study lineage, including prior stages, is represented by supplied manifests.",
    )
    return parser.parse_args()


def study_set(path: Path) -> tuple[pd.DataFrame, str, set[str]]:
    frame = load_table(path)
    column = resolve_column(frame, STUDY_COLUMNS, required=True, label="study identifier")
    assert column is not None
    return frame, column, normalized_ids(frame[column])


def stage_study_set(paths: list[Path], stage_name: str) -> set[str]:
    frame = load_tables(paths)
    success_candidates = {
        "downloaded_studies": ("exists", "download_ok", "downloaded"),
        "readable_dicoms": ("read_ok",),
        "cine_candidates": ("is_multiframe",),
        "extracted_clips": ("write_ok", "extract_ok"),
        "clip_embeddings": ("write_ok", "embedding_ok"),
    }.get(stage_name, ())
    success_col = resolve_column(frame, success_candidates) if success_candidates else None
    if success_candidates and success_col is None:
        raise ValueError(
            f"{stage_name} manifest is missing a required success flag; expected one of {success_candidates}"
        )
    if success_col:
        values = frame[success_col]
        if values.dtype == bool:
            mask = values.fillna(False)
        else:
            mask = values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
        frame = frame[mask].copy()
    study_col = resolve_column(frame, STUDY_COLUMNS, required=True, label=f"study identifier for {stage_name}")
    assert study_col is not None
    return normalized_ids(frame[study_col])


def classify_missing(
    study_id: str, stage_sets: list[tuple[str, set[str]]], *, lineage_complete: bool
) -> str:
    if not lineage_complete:
        return "STAGE_LINEAGE_INCOMPLETE"
    for stage_name, identifiers in stage_sets:
        if study_id not in identifiers:
            return f"ABSENT_FROM_{stage_name.upper()}"
    return "NO_STUDY_EMBEDDING_AFTER_SUPPLIED_STAGES"


def main() -> int:
    args = parse_args()
    missing_paths = [path for path in (args.selected_studies, args.study_embeddings) if not path.exists()]
    for name in (
        "eligible_all_studies",
        "prior_stage_studies",
        "downloaded_studies",
        "readable_dicoms",
        "cine_candidates",
        "extracted_clips",
        "clip_embeddings",
    ):
        missing_paths.extend(path for path in (getattr(args, name) or []) if not path.exists())
    if missing_paths:
        print(
            json.dumps(
                {"status": "BLOCKED_MISSING_INPUT", "n_missing_inputs": len(missing_paths)},
                indent=2,
            )
        )
        return 2

    selected_frame, selected_col, selected_ids = study_set(args.selected_studies)
    embedding_frame, embedding_col, embedding_ids = study_set(args.study_embeddings)
    selected_subject_col = resolve_column(selected_frame, SUBJECT_COLUMNS)
    embedding_subject_col = resolve_column(embedding_frame, SUBJECT_COLUMNS)

    optional_sets: dict[str, set[str]] = {}
    for name in (
        "eligible_all_studies",
        "prior_stage_studies",
        "downloaded_studies",
        "readable_dicoms",
        "cine_candidates",
        "extracted_clips",
        "clip_embeddings",
    ):
        paths = getattr(args, name)
        if paths:
            optional_sets[name] = stage_study_set(paths, name)

    missing_selected = sorted(selected_ids - embedding_ids)
    additional_embeddings = sorted(embedding_ids - selected_ids)
    stage_order = [
        (name, optional_sets[name])
        for name in (
            "downloaded_studies",
            "readable_dicoms",
            "cine_candidates",
            "extracted_clips",
            "clip_embeddings",
        )
        if name in optional_sets
    ]

    discrepancy_rows: list[dict[str, str]] = []
    for study_id in missing_selected:
        discrepancy_rows.append(
            {
                "study_id": study_id,
                "discrepancy": "SELECTED_WITHOUT_STUDY_EMBEDDING",
                "reason": classify_missing(
                    study_id, stage_order, lineage_complete=args.stage_lineage_complete
                ),
            }
        )
    for study_id in additional_embeddings:
        if study_id in optional_sets.get("prior_stage_studies", set()):
            reason = "PRIOR_STAGE_STUDY"
        elif study_id in optional_sets.get("eligible_all_studies", set()):
            reason = "ELIGIBLE_NONSELECTED_OR_REPEAT_STUDY"
        elif "eligible_all_studies" in optional_sets:
            reason = "OUTSIDE_SUPPLIED_ELIGIBLE_SET"
        else:
            reason = "SOURCE_UNRESOLVED"
        discrepancy_rows.append({"study_id": study_id, "discrepancy": "EMBEDDING_OUTSIDE_SELECTED_SET", "reason": reason})

    reason_counts = pd.DataFrame(discrepancy_rows)
    if reason_counts.empty:
        aggregate_reasons = pd.DataFrame(columns=["discrepancy", "reason", "n_studies"])
    else:
        aggregate_reasons = (
            reason_counts.groupby(["discrepancy", "reason"], dropna=False)
            .size()
            .reset_index(name="n_studies")
        )

    summary_row = pd.DataFrame(
        [
            {
                "n_selected_studies": len(selected_ids),
                "n_embedding_studies": len(embedding_ids),
                "n_selected_with_embedding": len(selected_ids & embedding_ids),
                "n_selected_without_embedding": len(missing_selected),
                "n_embeddings_outside_selected": len(additional_embeddings),
                "n_selected_subjects": int(selected_frame[selected_subject_col].nunique()) if selected_subject_col else None,
                "n_embedding_subjects": int(embedding_frame[embedding_subject_col].nunique()) if embedding_subject_col else None,
            }
        ]
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_aggregate_csv(summary_row, args.output_dir / "embedding_eligibility_overlap_summary.csv")
    write_aggregate_csv(aggregate_reasons, args.output_dir / "embedding_discrepancy_reason_counts.csv")

    restricted_written = False
    if args.restricted_output_dir is not None:
        restricted_dir = require_restricted_path(args.restricted_output_dir)
        pd.DataFrame(discrepancy_rows, columns=["study_id", "discrepancy", "reason"]).to_csv(
            restricted_dir / "embedding_eligibility_discrepancies_restricted.csv", index=False
        )
        restricted_written = True

    summary = {
        "audit": "embedding_eligibility_overlap",
        "n_selected_without_embedding": len(missing_selected),
        "n_embeddings_outside_selected": len(additional_embeddings),
        "restricted_discrepancy_file_written": restricted_written,
        "supplied_stage_manifests": sorted(optional_sets),
        "stage_lineage_complete_asserted": args.stage_lineage_complete,
        "stage_study_membership_rule": "AT_LEAST_ONE_SUCCESSFUL_ROW_FOR_FLAGGED_ROW_LEVEL_MANIFESTS",
        "patient_level_output_in_repository": False,
    }
    write_json(summary, args.output_dir / "embedding_eligibility_overlap.summary.json")
    print(json.dumps(summary, indent=2))
    return 1 if missing_selected or additional_embeddings else 0


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
