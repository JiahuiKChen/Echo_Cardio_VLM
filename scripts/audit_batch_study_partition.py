#!/usr/bin/env python3
"""Audit that fullscale batch-study manifests exactly partition the selected cohort.

Only aggregate counts and flags are emitted to the aggregate output. Study-level
discrepancies may be written only to an explicit restricted directory outside
the repository. No labels, predictions, embeddings, or performance are read.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

import pandas as pd

from lvef_multitask_audit_utils import (
    STUDY_COLUMNS,
    SUBJECT_COLUMNS,
    load_table,
    require_restricted_path,
    resolve_column,
    run_guarded,
    write_json,
)


MISSING_IDENTIFIER_TOKENS = {"null", "none", "nan", "<na>"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected-studies", type=Path, required=True)
    parser.add_argument("--prior-stage-studies", type=Path)
    parser.add_argument("--batch-manifest", type=Path, required=True)
    parser.add_argument("--batch-studies", type=Path, action="append", required=True)
    parser.add_argument("--expected-batches", type=int, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path)
    return parser.parse_args()


def canonical_identifier_series(series: pd.Series) -> pd.Series:
    def canonicalize(value: object) -> object:
        if pd.isna(value):
            return pd.NA
        text = str(value).strip()
        if not text or text.casefold() in MISSING_IDENTIFIER_TOKENS:
            return pd.NA
        try:
            numeric = Decimal(text)
        except InvalidOperation:
            return text
        if numeric.is_finite() and numeric == numeric.to_integral_value():
            return str(int(numeric))
        return text

    return series.map(canonicalize).astype("string")


def identifier_projection(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    subject_col = resolve_column(
        frame, SUBJECT_COLUMNS, required=True, label=f"subject identifier for {source}"
    )
    study_col = resolve_column(
        frame, STUDY_COLUMNS, required=True, label=f"study identifier for {source}"
    )
    assert subject_col is not None and study_col is not None
    return pd.DataFrame(
        {
            "_subject": canonical_identifier_series(frame[subject_col]),
            "_study": canonical_identifier_series(frame[study_col]),
        },
        index=frame.index,
    )


def manifest_structure(
    payload: Any, expected_batches: int
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("Batch manifest must be a JSON object")
    entries = payload.get("batches")
    if not isinstance(entries, list):
        raise ValueError("Batch manifest batches must be a list")
    parsed: dict[str, dict[str, Any]] = {}
    entry_schema_valid = True
    declared_names: list[str] = []
    per_entry_batch_ids_valid = True
    for item in entries:
        valid_item = (
            isinstance(item, dict)
            and isinstance(item.get("csv"), str)
            and type(item.get("batch_id")) is int
            and type(item.get("n_studies")) is int
            and item["n_studies"] >= 0
        )
        if not valid_item:
            entry_schema_valid = False
            continue
        name = Path(item["csv"]).name
        declared_names.append(name)
        expected_name = f"batch_{item['batch_id']:03d}_studies.csv"
        if name != expected_name or not 0 <= item["batch_id"] < expected_batches:
            per_entry_batch_ids_valid = False
        parsed[name] = {
            "batch_id": item["batch_id"],
            "n_studies": item["n_studies"],
        }

    expected_names = {f"batch_{index:03d}_studies.csv" for index in range(expected_batches)}
    declared_set = set(declared_names)
    top_counts_are_integers = all(
        type(payload.get(key)) is int
        for key in ("n_total", "n_already_done", "n_remaining", "n_batches")
    )
    structure = {
        "n_batch_entries_declared": len(entries),
        "n_unique_batch_names_declared": len(declared_set),
        "n_batches_expected": expected_batches,
        "batch_entry_schema_valid": entry_schema_valid,
        "batch_entry_count_matches_expected": len(entries) == expected_batches,
        "batch_names_unique": len(declared_names) == len(declared_set),
        "declared_names_match_expected": declared_set == expected_names,
        "batch_ids_and_names_match": per_entry_batch_ids_valid,
        "top_level_counts_present_as_integers": top_counts_are_integers,
        "top_level_n_batches_matches_expected": (
            top_counts_are_integers and payload["n_batches"] == expected_batches
        ),
    }
    structure["manifest_structure_valid"] = all(
        structure[key]
        for key in (
            "batch_entry_schema_valid",
            "batch_entry_count_matches_expected",
            "batch_names_unique",
            "declared_names_match_expected",
            "batch_ids_and_names_match",
            "top_level_counts_present_as_integers",
            "top_level_n_batches_matches_expected",
        )
    )
    return structure, parsed


def evaluate_partition(
    *,
    selected_frame: pd.DataFrame,
    prior_frame: pd.DataFrame | None,
    batch_frames: dict[str, pd.DataFrame],
    manifest_payload: Any,
    expected_batches: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    structure, declared = manifest_structure(manifest_payload, expected_batches)
    selected = identifier_projection(selected_frame, "selected cohort")
    selected_missing = selected[["_subject", "_study"]].isna().any(axis=1)
    selected_valid = selected.loc[~selected_missing]
    selected_ids = set(selected_valid["_study"].astype(str))
    selected_subject_map = dict(
        selected_valid.drop_duplicates("_study").set_index("_study")["_subject"].astype(str)
    )

    observed_names = set(batch_frames)
    expected_names = {f"batch_{index:03d}_studies.csv" for index in range(expected_batches)}
    batch_parts: list[pd.DataFrame] = []
    entry_row_counts_match = True
    restricted: list[dict[str, str]] = []
    for name, frame in sorted(batch_frames.items()):
        projected = identifier_projection(frame, name).assign(_batch=name)
        projected["_source_row"] = projected.index.astype(str)
        batch_parts.append(projected)
        if name not in declared or declared[name]["n_studies"] != len(frame):
            entry_row_counts_match = False
    batches = (
        pd.concat(batch_parts, ignore_index=True)
        if batch_parts
        else pd.DataFrame(columns=["_subject", "_study", "_batch", "_source_row"])
    )
    batch_missing = batches[["_subject", "_study"]].isna().any(axis=1)
    valid_batches = batches.loc[~batch_missing].copy()
    batch_ids = set(valid_batches["_study"].astype(str))
    duplicated_mask = valid_batches["_study"].duplicated(keep=False)
    duplicate_ids = set(valid_batches.loc[duplicated_mask, "_study"].astype(str))
    subject_counts = valid_batches.groupby("_study")["_subject"].nunique(dropna=False)
    multiple_subject_ids = set(subject_counts[subject_counts > 1].index.astype(str))
    expected_subjects = valid_batches["_study"].map(selected_subject_map).astype("string")
    selected_mapping_mask = valid_batches["_study"].isin(selected_ids)
    mapping_mismatch_mask = selected_mapping_mask & valid_batches["_subject"].ne(
        expected_subjects
    ).fillna(True)
    mapping_mismatch_ids = set(
        valid_batches.loc[mapping_mismatch_mask, "_study"].astype(str)
    )

    prior_present = prior_frame is not None
    prior_ids: set[str] = set()
    prior_missing_count: int | None = None
    prior_duplicate_ids: set[str] = set()
    prior_mapping_mismatch_ids: set[str] = set()
    if prior_frame is not None:
        prior = identifier_projection(prior_frame, "prior stage")
        prior_missing = prior[["_subject", "_study"]].isna().any(axis=1)
        prior_missing_count = int(prior_missing.sum())
        prior_valid = prior.loc[~prior_missing]
        prior_ids = set(prior_valid["_study"].astype(str))
        prior_duplicate_ids = set(
            prior_valid.loc[prior_valid["_study"].duplicated(keep=False), "_study"].astype(str)
        )
        expected_prior_subjects = prior_valid["_study"].map(selected_subject_map).astype("string")
        prior_selected_mask = prior_valid["_study"].isin(selected_ids)
        prior_mapping_mismatch_ids = set(
            prior_valid.loc[
                prior_selected_mask
                & prior_valid["_subject"].ne(expected_prior_subjects).fillna(True),
                "_study",
            ].astype(str)
        )

    expected_batch_ids = selected_ids - prior_ids if prior_present else set()
    missing_expected = expected_batch_ids - batch_ids if prior_present else set()
    extra_batch = batch_ids - expected_batch_ids if prior_present else set()
    payload = manifest_payload if isinstance(manifest_payload, dict) else {}
    top_count_identity_valid = bool(
        structure["top_level_counts_present_as_integers"]
        and payload.get("n_total") == len(selected_ids)
        and (
            not prior_present
            or (
                payload.get("n_already_done") == len(prior_ids)
                and payload.get("n_remaining") == len(expected_batch_ids)
            )
        )
    )
    observed_files_valid = observed_names == expected_names
    batch_identifier_integrity_valid = bool(
        not batch_missing.any()
        and not duplicate_ids
        and not multiple_subject_ids
        and not mapping_mismatch_ids
    )
    prior_identifier_integrity_valid = bool(
        prior_present
        and prior_missing_count == 0
        and not prior_duplicate_ids
        and not prior_mapping_mismatch_ids
    )
    partition_valid = bool(
        prior_present
        and structure["manifest_structure_valid"]
        and observed_files_valid
        and entry_row_counts_match
        and top_count_identity_valid
        and batch_identifier_integrity_valid
        and prior_identifier_integrity_valid
        and not selected_missing.any()
        and not missing_expected
        and not extra_batch
    )

    for status, identifiers in (
        ("DUPLICATE_BATCH_STUDY_ASSIGNMENT", duplicate_ids),
        ("BATCH_STUDY_MAPPED_TO_MULTIPLE_SUBJECTS", multiple_subject_ids),
        ("BATCH_STUDY_SUBJECT_MISMATCH", mapping_mismatch_ids),
        ("EXPECTED_SELECTED_STUDY_MISSING_FROM_BATCHES", missing_expected),
        ("BATCH_STUDY_OUTSIDE_SELECTED_MINUS_PRIOR", extra_batch),
        ("DUPLICATE_PRIOR_STAGE_STUDY", prior_duplicate_ids),
        ("PRIOR_STAGE_STUDY_SUBJECT_MISMATCH", prior_mapping_mismatch_ids),
    ):
        restricted.extend(
            {"status": status, "study_id": identifier, "batch_name": "", "source_row": ""}
            for identifier in sorted(identifiers)
        )
    for _, row in batches.loc[batch_missing].iterrows():
        restricted.append(
            {
                "status": "BATCH_ROW_MISSING_SUBJECT_OR_STUDY",
                "study_id": "" if pd.isna(row["_study"]) else str(row["_study"]),
                "batch_name": str(row["_batch"]),
                "source_row": str(row["_source_row"]),
            }
        )

    summary = {
        "audit": "batch_study_partition",
        **structure,
        "n_batch_files_observed": len(observed_names),
        "observed_batch_names_match_expected": observed_files_valid,
        "declared_entry_row_counts_match_files": entry_row_counts_match,
        "prior_stage_manifest_present": prior_present,
        "partition_evaluable": prior_present,
        "n_selected_rows": int(len(selected_frame)),
        "n_selected_rows_missing_subject_or_study": int(selected_missing.sum()),
        "n_selected_studies": len(selected_ids),
        "n_prior_stage_studies": len(prior_ids) if prior_present else None,
        "n_prior_stage_studies_in_selected": (
            len(prior_ids & selected_ids) if prior_present else None
        ),
        "n_prior_stage_studies_outside_selected": (
            len(prior_ids - selected_ids) if prior_present else None
        ),
        "n_prior_rows_missing_subject_or_study": prior_missing_count,
        "n_prior_duplicate_studies": len(prior_duplicate_ids) if prior_present else None,
        "n_prior_selected_subject_mapping_mismatches": (
            len(prior_mapping_mismatch_ids) if prior_present else None
        ),
        "n_batch_rows": int(len(batches)),
        "n_batch_rows_missing_subject_or_study": int(batch_missing.sum()),
        "n_unique_batch_studies": len(batch_ids),
        "n_duplicate_batch_study_assignments": len(duplicate_ids),
        "n_batch_studies_with_multiple_subjects": len(multiple_subject_ids),
        "n_batch_selected_subject_mapping_mismatches": len(mapping_mismatch_ids),
        "n_expected_selected_minus_prior_studies": (
            len(expected_batch_ids) if prior_present else None
        ),
        "n_expected_studies_missing_from_batches": (
            len(missing_expected) if prior_present else None
        ),
        "n_batch_studies_outside_selected_minus_prior": (
            len(extra_batch) if prior_present else None
        ),
        "top_level_manifest_counts_match_sources": top_count_identity_valid,
        "batch_identifier_integrity_valid": batch_identifier_integrity_valid,
        "prior_identifier_integrity_valid": prior_identifier_integrity_valid,
        "selected_batch_partition_valid": partition_valid,
        "source_values_emitted": False,
    }
    return summary, restricted


def main() -> int:
    args = parse_args()
    required_paths = [args.selected_studies, args.batch_manifest, *args.batch_studies]
    if args.prior_stage_studies is not None:
        required_paths.append(args.prior_stage_studies)
    missing_required = sum(not path.is_file() for path in required_paths)
    if missing_required:
        print(json.dumps({"status": "BLOCKED_MISSING_INPUT", "n_missing_inputs": missing_required}))
        return 2
    selected = load_table(args.selected_studies)
    prior = (
        load_table(args.prior_stage_studies)
        if args.prior_stage_studies is not None
        else None
    )
    batch_frames = {path.name: load_table(path) for path in args.batch_studies}
    payload = json.loads(args.batch_manifest.read_text())
    summary, restricted_rows = evaluate_partition(
        selected_frame=selected,
        prior_frame=prior,
        batch_frames=batch_frames,
        manifest_payload=payload,
        expected_batches=args.expected_batches,
    )
    restricted_written = False
    if args.restricted_output_dir is not None:
        restricted_dir = require_restricted_path(args.restricted_output_dir)
        pd.DataFrame(
            restricted_rows,
            columns=["status", "study_id", "batch_name", "source_row"],
        ).to_csv(restricted_dir / "batch_study_partition_discrepancies_restricted.csv", index=False)
        restricted_written = True
    summary["restricted_discrepancies_written"] = restricted_written
    write_json(summary, args.output_json)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["selected_batch_partition_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
