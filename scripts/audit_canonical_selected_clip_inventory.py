"""Inventory a model-independent canonical selected-cohort clip proposal.

The audit reads successful component clip and extraction manifests but never
opens embedding arrays, outcomes, predictions, or performance. It determines
which selected physical-source locators have a surviving extracted NPZ, only a
retained source DICOM, or neither; proposes one source row where the evidence is
unambiguous; and quarantines ambiguous provenance. Restricted identifiers and
locators remain outside Git. The aggregate directory receives exactly three
fixed-schema outputs.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from audit_lvef_multitask_artifacts import _canonical_manifest_scalar
from lvef_multitask_audit_utils import (
    load_table,
    require_restricted_path,
    run_guarded,
    write_aggregate_csv,
    write_json,
)
from lvef_multitask_clip_provenance import (
    DICOM_HASH_COLUMNS,
    DICOM_LOCATOR_COLUMNS,
    EXPECTED_COMPONENTS,
    NPZ_HASH_COLUMNS,
    NPZ_LOCATOR_COLUMNS,
    canonical_subject_study,
    dicom_audit_lookup,
    extraction_lookup,
    extraction_records_for_row,
    first_locator,
    first_manifest_hash,
    named_component_paths,
    parse_path_rewrites,
    prepared_clip_manifest,
    resolve_locator_path,
    selected_study_map,
    sha256_file,
)


BY_COMPONENT_COLUMNS: tuple[str, ...] = (
    "component",
    "n_embedded_rows",
    "n_selected_rows",
    "n_outside_selected_rows_excluded",
    "n_ownership_mismatch_rows_quarantined",
    "n_selected_physical_source_groups",
    "n_selected_studies_with_embedded_clip",
    "n_physical_sources_with_surviving_npz",
    "n_physical_sources_with_source_dicom_no_npz",
    "n_physical_sources_with_neither",
    "n_proposed_canonical_rows",
    "n_exact_deduplication_rows_removed",
    "n_quarantined_physical_source_groups",
    "n_quarantined_rows",
    "n_sources_requiring_npz_reextraction",
    "n_sources_requiring_dicom_redownload",
)

AGGREGATE_FILENAMES: tuple[str, ...] = (
    "canonical_selected_clip_inventory.summary.json",
    "canonical_selected_clip_inventory_by_component.csv",
    "canonical_selected_clip_inventory_safety_gate.json",
)
FORBIDDEN_AGGREGATE_TEXT = re.compile(
    r"(?:subject_id|study_id|physical_source_key|dicom_filepath|npz_path|"
    r"embedding_idx|/restricted/|/Users/|[0-9a-f]{64})",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component-manifest", action="append", required=True)
    parser.add_argument("--component-extraction-manifest", action="append", required=True)
    parser.add_argument("--component-dicom-audit", action="append", required=True)
    parser.add_argument("--selected-studies", type=Path, required=True)
    parser.add_argument("--dicom-root", type=Path, action="append", default=[])
    parser.add_argument("--path-rewrite", action="append", default=[], metavar="FROM=TO")
    parser.add_argument(
        "--hash-mode",
        choices=("none", "duplicates", "all"),
        default="duplicates",
        help="File hashes never enter aggregate output; duplicates is the default I/O bound.",
    )
    parser.add_argument("--expected-selected-imaging-studies", type=int, default=4525)
    parser.add_argument("--aggregate-output-dir", type=Path, required=True)
    parser.add_argument("--restricted-output-dir", type=Path, required=True)
    return parser.parse_args()


def _choose_locator(
    records: Sequence[Mapping[str, object]],
    candidates: Sequence[str],
    *,
    roots: Sequence[Path],
    rewrites: Sequence[tuple[str, str]],
) -> tuple[str | None, str | None, Path | None]:
    possibilities: list[tuple[str, str, Path | None]] = []
    for record in records:
        locator, column = first_locator([record], candidates)
        if locator is None or column is None:
            continue
        path = resolve_locator_path(locator, roots=roots, rewrites=rewrites)
        item = (locator, column, path)
        if item not in possibilities:
            possibilities.append(item)
    if not possibilities:
        return None, None, None
    return next(
        (item for item in possibilities if item[2] is not None and item[2].is_file()),
        possibilities[0],
    )


def _canonical_payload_tuple(row: Mapping[str, object]) -> tuple[str, ...]:
    excluded = {
        "embedding_idx",
        "embedding_l2_norm",
        "_audit_subject",
        "_audit_study",
        "_component",
        "_component_row",
    }
    columns = sorted(column for column in row if column not in excluded)
    return tuple(_canonical_manifest_scalar(row.get(column)) for column in columns)


def _row_record(
    row: pd.Series,
    extraction_records: Sequence[Mapping[str, object]],
    dicom_records: Sequence[Mapping[str, object]],
    *,
    component: str,
    selected: Mapping[str, str],
    dicom_roots: Sequence[Path],
    rewrites: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    source = row.to_dict()
    records: list[Mapping[str, object]] = [source, *extraction_records, *dicom_records]
    dicom_locator, dicom_column, dicom_path = _choose_locator(
        records,
        DICOM_LOCATOR_COLUMNS,
        roots=dicom_roots,
        rewrites=rewrites,
    )
    npz_locator, npz_column, npz_path = _choose_locator(
        records,
        NPZ_LOCATOR_COLUMNS,
        roots=(),
        rewrites=rewrites,
    )
    dicom_manifest_hash, dicom_hash_column = first_manifest_hash(
        records, DICOM_HASH_COLUMNS
    )
    npz_manifest_hash, npz_hash_column = first_manifest_hash(records, NPZ_HASH_COLUMNS)
    subject = str(row["_audit_subject"])
    study = str(row["_audit_study"])
    if study not in selected:
        scope = "outside_selected"
    elif selected[study] != subject:
        scope = "ownership_mismatch"
    else:
        scope = "selected"
    return {
        "component": component,
        "component_row": int(row["_component_row"]),
        "subject_id": subject,
        "study_id": study,
        "selected_scope": scope,
        "dicom_locator": dicom_locator,
        "dicom_locator_source_column": dicom_column,
        "dicom_resolved_path": None if dicom_path is None else str(dicom_path),
        "dicom_exists": bool(dicom_path is not None and dicom_path.is_file()),
        "dicom_manifest_sha256": dicom_manifest_hash,
        "dicom_manifest_hash_source_column": dicom_hash_column,
        "npz_locator": npz_locator,
        "npz_locator_source_column": npz_column,
        "npz_resolved_path": None if npz_path is None else str(npz_path),
        "npz_exists": bool(npz_path is not None and npz_path.is_file()),
        "npz_manifest_sha256": npz_manifest_hash,
        "npz_manifest_hash_source_column": npz_hash_column,
        "extraction_match_count": len(extraction_records),
        "payload_tuple": _canonical_payload_tuple(source),
    }


def _physical_source_key(row: Mapping[str, object]) -> tuple[str, ...]:
    subject = str(row["subject_id"])
    study = str(row["study_id"])
    if row.get("dicom_locator") is not None:
        return ("DICOM", subject, study, str(row["dicom_locator"]))
    if row.get("npz_locator") is not None:
        return ("NPZ_ONLY", subject, study, str(row["npz_locator"]))
    return (
        "MISSING_LOCATOR",
        subject,
        study,
        str(row["component"]),
        str(row["component_row"]),
    )


def _hash_for_row(row: Mapping[str, object], *, enabled: bool) -> tuple[str | None, str | None]:
    if not enabled:
        return None, None
    npz_path = (
        Path(str(row["npz_resolved_path"]))
        if row.get("npz_resolved_path") is not None
        else None
    )
    dicom_path = (
        Path(str(row["dicom_resolved_path"]))
        if row.get("dicom_resolved_path") is not None
        else None
    )
    return sha256_file(dicom_path), sha256_file(npz_path)


def _adjudicate_physical_group(
    key: tuple[str, ...],
    rows: Sequence[Mapping[str, object]],
    *,
    hash_mode: str,
) -> dict[str, Any]:
    components = sorted({str(row["component"]) for row in rows})
    npz_exists = any(bool(row["npz_exists"]) for row in rows)
    dicom_exists = any(bool(row["dicom_exists"]) for row in rows)
    availability = (
        "SURVIVING_NPZ"
        if npz_exists
        else "SOURCE_DICOM_NO_NPZ"
        if dicom_exists
        else "NEITHER"
    )
    duplicate = len(rows) > 1
    compute_hash = hash_mode == "all" or (hash_mode == "duplicates" and duplicate)
    computed = [_hash_for_row(row, enabled=compute_hash) for row in rows]
    dicom_hashes = [value[0] for value in computed if value[0] is not None]
    npz_hashes = [value[1] for value in computed if value[1] is not None]
    payload_equal = len({tuple(row["payload_tuple"]) for row in rows}) == 1
    locator_missing = key[0] == "MISSING_LOCATOR"
    cross_component = len(components) > 1
    content_confirmed = bool(dicom_hashes or npz_hashes) and (
        len(set(dicom_hashes)) <= 1 and len(set(npz_hashes)) <= 1
    )

    if locator_missing:
        disposition = "QUARANTINE_MISSING_SOURCE_LOCATOR"
        proposed_rows = 0
        removed = 0
        quarantine = True
    elif cross_component:
        disposition = "QUARANTINE_CROSS_COMPONENT_SOURCE_COLLISION"
        proposed_rows = 0
        removed = 0
        quarantine = True
    elif duplicate and payload_equal and content_confirmed:
        disposition = "EXACT_SOURCE_ROW_DEDUPLICATION_PROPOSED"
        proposed_rows = 1
        removed = len(rows) - 1
        quarantine = False
    elif duplicate:
        disposition = "QUARANTINE_DUPLICATE_SOURCE_EVIDENCE_INCOMPLETE_OR_DIFFERENT"
        proposed_rows = 0
        removed = 0
        quarantine = True
    else:
        disposition = "KEEP_SINGLE_PHYSICAL_SOURCE_ROW"
        proposed_rows = 1
        removed = 0
        quarantine = False

    return {
        "physical_source_key": json.dumps(key, separators=(",", ":")),
        "subject_id": str(rows[0]["subject_id"]),
        "study_id": str(rows[0]["study_id"]),
        "components": ";".join(components),
        "n_embedded_rows": len(rows),
        "source_availability": availability,
        "any_npz_exists": npz_exists,
        "any_dicom_exists": dicom_exists,
        "payload_equal_excluding_embedding_index_and_l2": payload_equal,
        "content_hash_confirmation_available": content_confirmed,
        "dicom_hashes_json": json.dumps(dicom_hashes),
        "npz_hashes_json": json.dumps(npz_hashes),
        "dicom_locators_json": json.dumps([row.get("dicom_locator") for row in rows]),
        "npz_locators_json": json.dumps([row.get("npz_locator") for row in rows]),
        "dicom_resolved_paths_json": json.dumps(
            [row.get("dicom_resolved_path") for row in rows]
        ),
        "npz_resolved_paths_json": json.dumps(
            [row.get("npz_resolved_path") for row in rows]
        ),
        "proposed_disposition": disposition,
        "n_proposed_canonical_rows": proposed_rows,
        "n_exact_deduplication_rows_removed": removed,
        "quarantine_required": quarantine,
        "requires_npz_reextraction": not npz_exists and dicom_exists,
        "requires_dicom_redownload": not npz_exists and not dicom_exists,
        "vector_evidence_used": False,
        "canonical_store_mutated": False,
    }


def _component_memberships(group: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(str(group["components"]).split(";"))


def _safe_component_rows(
    raw_rows: Sequence[Mapping[str, object]],
    groups: Sequence[Mapping[str, object]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for component in EXPECTED_COMPONENTS:
        component_raw = [row for row in raw_rows if row["component"] == component]
        selected_raw = [row for row in component_raw if row["selected_scope"] == "selected"]
        outside = [row for row in component_raw if row["selected_scope"] == "outside_selected"]
        ownership = [
            row for row in component_raw if row["selected_scope"] == "ownership_mismatch"
        ]
        member_groups = [group for group in groups if component in _component_memberships(group)]
        rows.append(
            {
                "component": component,
                "n_embedded_rows": len(component_raw),
                "n_selected_rows": len(selected_raw),
                "n_outside_selected_rows_excluded": len(outside),
                "n_ownership_mismatch_rows_quarantined": len(ownership),
                "n_selected_physical_source_groups": len(member_groups),
                "n_selected_studies_with_embedded_clip": len(
                    {row["study_id"] for row in selected_raw}
                ),
                "n_physical_sources_with_surviving_npz": sum(
                    group["source_availability"] == "SURVIVING_NPZ"
                    for group in member_groups
                ),
                "n_physical_sources_with_source_dicom_no_npz": sum(
                    group["source_availability"] == "SOURCE_DICOM_NO_NPZ"
                    for group in member_groups
                ),
                "n_physical_sources_with_neither": sum(
                    group["source_availability"] == "NEITHER"
                    for group in member_groups
                ),
                "n_proposed_canonical_rows": sum(
                    int(group["n_proposed_canonical_rows"]) for group in member_groups
                ),
                "n_exact_deduplication_rows_removed": sum(
                    int(group["n_exact_deduplication_rows_removed"])
                    for group in member_groups
                ),
                "n_quarantined_physical_source_groups": sum(
                    bool(group["quarantine_required"]) for group in member_groups
                ),
                "n_quarantined_rows": sum(
                    int(group["n_embedded_rows"])
                    for group in member_groups
                    if bool(group["quarantine_required"])
                )
                + len(ownership),
                "n_sources_requiring_npz_reextraction": sum(
                    bool(group["requires_npz_reextraction"]) for group in member_groups
                ),
                "n_sources_requiring_dicom_redownload": sum(
                    bool(group["requires_dicom_redownload"]) for group in member_groups
                ),
            }
        )
    return pd.DataFrame(rows, columns=BY_COMPONENT_COLUMNS)


def _target_paths_available(root: Path) -> None:
    if any((root / name).exists() for name in AGGREGATE_FILENAMES):
        raise ValueError("Phase 1D canonical-inventory aggregate output already exists")


def _validate_written_aggregate_outputs(
    root: Path, by_component: pd.DataFrame
) -> None:
    expected_before_safety = set(AGGREGATE_FILENAMES) - {
        "canonical_selected_clip_inventory_safety_gate.json"
    }
    actual = {path.name for path in root.iterdir() if path.is_file()}
    if actual != expected_before_safety:
        raise ValueError("Canonical inventory did not emit the exact pre-safety output set")
    if list(by_component.columns) != list(BY_COMPONENT_COLUMNS):
        raise ValueError("Unexpected canonical inventory component schema")
    if list(by_component["component"]) != list(EXPECTED_COMPONENTS):
        raise ValueError("Canonical inventory component vocabulary/order differs")
    numeric = by_component.drop(columns=["component"])
    if numeric.isna().any().any() or (numeric < 0).any().any():
        raise ValueError("Canonical inventory aggregate counts are invalid")
    aggregate_text = "\n".join(
        (root / name).read_text(errors="replace") for name in sorted(actual)
    )
    if FORBIDDEN_AGGREGATE_TEXT.search(aggregate_text):
        raise ValueError("Restricted token or value detected in inventory aggregate output")


def main() -> int:
    args = parse_args()
    if args.expected_selected_imaging_studies <= 0:
        raise ValueError("Expected imaging-study count must be positive")
    manifests = named_component_paths(
        args.component_manifest, label="clip manifest"
    )
    extractions = named_component_paths(
        args.component_extraction_manifest, label="extraction manifest"
    )
    dicom_audits = named_component_paths(
        args.component_dicom_audit, label="DICOM audit"
    )
    if set(manifests) != set(extractions) or set(manifests) != set(dicom_audits):
        raise ValueError("Clip, extraction, and DICOM-audit component labels differ")
    if not args.selected_studies.is_file() or not all(
        path.is_file()
        for path in [
            *manifests.values(),
            *extractions.values(),
            *dicom_audits.values(),
        ]
    ):
        raise FileNotFoundError("A required canonical-inventory input is unavailable")

    rewrites = parse_path_rewrites(args.path_rewrite)
    dicom_roots = tuple(path.expanduser() for path in args.dicom_root)
    selected = selected_study_map(load_table(args.selected_studies))
    extraction_lookups = {
        name: extraction_lookup(load_table(path)) for name, path in extractions.items()
    }
    dicom_lookups = {
        name: dicom_audit_lookup(load_table(path))
        for name, path in dicom_audits.items()
    }

    raw_rows: list[dict[str, Any]] = []
    for component in EXPECTED_COMPONENTS:
        frame = prepared_clip_manifest(manifests[component])
        ids = canonical_subject_study(frame)
        work = frame.copy()
        work["_audit_subject"] = ids["subject_id"]
        work["_audit_study"] = ids["study_id"]
        work["_component"] = component
        work["_component_row"] = [int(index) for index in work.index]
        for _, row in work.iterrows():
            extraction_records = extraction_records_for_row(
                row, extraction_lookups[component]
            )
            dicom_records = extraction_records_for_row(
                row, dicom_lookups[component]
            )
            raw_rows.append(
                _row_record(
                    row,
                    extraction_records,
                    dicom_records,
                    component=component,
                    selected=selected,
                    dicom_roots=dicom_roots,
                    rewrites=rewrites,
                )
            )

    selected_rows = [row for row in raw_rows if row["selected_scope"] == "selected"]
    grouped: defaultdict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in selected_rows:
        grouped[_physical_source_key(row)].append(row)
    groups = [
        _adjudicate_physical_group(key, rows, hash_mode=args.hash_mode)
        for key, rows in sorted(grouped.items())
    ]
    by_component = _safe_component_rows(raw_rows, groups)

    proposed_studies = {
        str(group["study_id"])
        for group in groups
        if int(group["n_proposed_canonical_rows"]) == 1
    }
    seen_selected_studies = {str(row["study_id"]) for row in selected_rows}
    reextract_components = sorted(
        {
            component
            for group in groups
            if bool(group["requires_npz_reextraction"])
            for component in _component_memberships(group)
        }
    )
    redownload_components = sorted(
        {
            component
            for group in groups
            if bool(group["requires_dicom_redownload"])
            for component in _component_memberships(group)
        }
    )
    n_quarantine = sum(bool(group["quarantine_required"]) for group in groups)
    n_surviving = sum(
        group["source_availability"] == "SURVIVING_NPZ" for group in groups
    )
    n_dicom_only = sum(
        group["source_availability"] == "SOURCE_DICOM_NO_NPZ" for group in groups
    )
    n_neither = sum(group["source_availability"] == "NEITHER" for group in groups)
    count_matches = len(proposed_studies) == args.expected_selected_imaging_studies
    if n_quarantine:
        availability_path = "BLOCKED_PENDING_QUARANTINED_SOURCE_RESOLUTION"
    elif n_neither:
        availability_path = "C3_SELECTED_ONLY_REDOWNLOAD_AND_CLEAN_RESTART"
    elif n_dicom_only:
        availability_path = "C2_SELECTED_ONLY_REEXTRACTION_FROM_RETAINED_DICOM"
    else:
        availability_path = (
            "C1_SELECTED_ONLY_REBUILD_FROM_SURVIVING_EXTRACTED_CLIPS_"
            "FILE_AVAILABILITY_COMPATIBLE"
        )

    summary = {
        "audit": "canonical_selected_clip_inventory",
        "status": "COMPLETE_MODEL_INDEPENDENT_INVENTORY"
        if count_matches
        else "BLOCKED_SELECTED_IMAGING_STUDY_COUNT_MISMATCH",
        "n_expected_components": len(EXPECTED_COMPONENTS),
        "n_selected_subject_study_rows": len(selected),
        "n_selected_studies_seen_in_embedding_manifests": len(seen_selected_studies),
        "n_selected_studies_with_at_least_one_proposed_canonical_clip": len(
            proposed_studies
        ),
        "n_expected_selected_imaging_studies": args.expected_selected_imaging_studies,
        "expected_selected_imaging_study_count_matches": count_matches,
        "n_selected_studies_without_embedded_clip": len(selected)
        - len(seen_selected_studies),
        "n_selected_embedded_rows": len(selected_rows),
        "n_outside_selected_rows_excluded": sum(
            row["selected_scope"] == "outside_selected" for row in raw_rows
        ),
        "n_ownership_mismatch_rows_quarantined": sum(
            row["selected_scope"] == "ownership_mismatch" for row in raw_rows
        ),
        "n_selected_physical_source_groups": len(groups),
        "n_physical_sources_with_surviving_npz": n_surviving,
        "n_physical_sources_with_source_dicom_no_npz": n_dicom_only,
        "n_physical_sources_with_neither": n_neither,
        "n_proposed_canonical_rows": sum(
            int(group["n_proposed_canonical_rows"]) for group in groups
        ),
        "n_exact_deduplication_rows_removed": sum(
            int(group["n_exact_deduplication_rows_removed"]) for group in groups
        ),
        "n_quarantined_physical_source_groups": n_quarantine,
        "components_requiring_npz_reextraction": reextract_components,
        "components_requiring_dicom_redownload": redownload_components,
        "complete_selected_rebuild_from_surviving_extracted_clips_file_availability_compatible": (
            n_dicom_only == 0 and n_neither == 0 and n_quarantine == 0 and count_matches
        ),
        "cohort_wide_npz_readability_validated": False,
        "cohort_wide_npz_content_hash_validated": args.hash_mode == "all",
        "c1_requires_followup_npz_readability_and_content_validation": True,
        "only_batch_000_reextraction_sufficient": reextract_components
        == ["batch_000"]
        and not redownload_components
        and n_quarantine == 0,
        "all_batches_000_008_reextraction_required": set(reextract_components)
        == {f"batch_{index:03d}" for index in range(9)},
        "source_dicom_redownload_required": bool(redownload_components),
        "proposed_availability_path": availability_path,
        "hash_mode": args.hash_mode,
        "vector_arrays_read": False,
        "outcomes_or_predictions_read": False,
        "canonical_store_mutated": False,
        "embedding_regeneration_authorized": False,
    }

    restricted_dir = require_restricted_path(args.restricted_output_dir)
    aggregate_dir = args.aggregate_output_dir.expanduser().resolve()
    if (
        restricted_dir == aggregate_dir
        or restricted_dir in aggregate_dir.parents
        or aggregate_dir in restricted_dir.parents
    ):
        raise ValueError("Aggregate and restricted output trees must be disjoint")
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    _target_paths_available(aggregate_dir)
    pd.DataFrame(raw_rows).to_csv(
        restricted_dir / "canonical_selected_clip_inventory_rows_restricted.csv",
        index=False,
    )
    pd.DataFrame(groups).to_csv(
        restricted_dir / "canonical_selected_clip_inventory_restricted.csv", index=False
    )
    write_json(summary, aggregate_dir / "canonical_selected_clip_inventory.summary.json")
    write_aggregate_csv(
        by_component,
        aggregate_dir / "canonical_selected_clip_inventory_by_component.csv",
    )
    _validate_written_aggregate_outputs(aggregate_dir, by_component)
    safety = {
        "audit": "canonical_selected_clip_inventory_safety_gate",
        "status": "PASS",
        "n_expected_aggregate_outputs": len(AGGREGATE_FILENAMES),
        "aggregate_output_names_exact": True,
        "by_component_schema_exact": True,
        "component_vocabulary_exact": True,
        "aggregate_content_allowlist_validated": True,
        "forbidden_token_scan_passed": True,
        "identifier_values_emitted": False,
        "locator_values_emitted": False,
        "file_hash_values_emitted": False,
        "restricted_records_written_outside_repository": True,
        "aggregate_safety_gate_passed": True,
    }
    write_json(
        safety,
        aggregate_dir / "canonical_selected_clip_inventory_safety_gate.json",
    )
    print(json.dumps(summary, sort_keys=True))
    return 0 if count_matches else 1


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
