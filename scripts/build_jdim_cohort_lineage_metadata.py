#!/usr/bin/env python3
"""Build path-free pinned lineage metadata for JDIM cohort reconstruction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from jdim_tier1.safety import (
    SAFE_ROLE_PATTERN,
    canonical_id_set_sha256,
    parse_named_paths,
    sha256_file,
    write_json,
)


DECLARED_LEGACY_SCOPE_VERSION = "jdim-declared-legacy-scope-v1"


def parse_name_class(values: list[str]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"--batch-source requires NAME=CLASS: {value!r}")
        name, source_class = value.split("=", 1)
        if not SAFE_ROLE_PATTERN.fullmatch(name):
            raise ValueError(f"Invalid path-like batch name: {name!r}")
        if source_class not in {"legacy", "fullscale"}:
            raise ValueError(f"Invalid source class for {name}: {source_class!r}")
        if name in out:
            raise ValueError(f"Duplicate batch source name: {name}")
        out[name] = {"source_class": source_class}
    if not out:
        raise ValueError("At least one --batch-source is required")
    return out


def parse_overlap_pairs(values: list[str], batch_names: set[str]) -> list[str]:
    pairs: list[str] = []
    for value in values:
        pieces = value.split("|")
        if (
            len(pieces) != 2
            or pieces[0] == pieces[1]
            or pieces[0] not in batch_names
            or pieces[1] not in batch_names
        ):
            raise ValueError(f"Invalid declared overlap pair: {value!r}")
        pairs.append("|".join(sorted(pieces)))
    return sorted(set(pairs))


def parse_outside_universe_batches(
    values: list[str],
    batches: dict[str, dict[str, str]],
) -> set[str]:
    declared: set[str] = set()
    for value in values:
        if value not in batches:
            raise ValueError(f"Unknown outside-universe batch: {value!r}")
        if batches[value]["source_class"] != "legacy":
            raise ValueError(
                f"Outside-universe retention may be declared only for legacy batches: {value!r}"
            )
        declared.add(value)
    return declared


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mimic-iv-echo-release", required=True)
    parser.add_argument("--source-denominator-definition", required=True)
    parser.add_argument("--imaging-lineage", required=True)
    parser.add_argument("--label-lineage", required=True)
    parser.add_argument("--split-map-csv", type=Path, required=True)
    parser.add_argument("--split-version", required=True)
    parser.add_argument("--split-generator", required=True)
    parser.add_argument("--batch-source", action="append", default=[], metavar="NAME=CLASS")
    parser.add_argument("--allow-batch-overlap", action="append", default=[], metavar="LEFT|RIGHT")
    parser.add_argument(
        "--allow-outside-universe-batch",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Declare a legacy batch whose outside-canonical-universe studies are retained "
            "for provenance and must reconcile exactly with the final embedding manifest."
        ),
    )
    parser.add_argument(
        "--selected-studies-csv",
        type=Path,
        default=None,
        help="Selected canonical study universe used to hash declared legacy scope.",
    )
    parser.add_argument(
        "--selected-universe-selection-rule",
        default=None,
        help="Path-free description of the deterministic selected-universe rule.",
    )
    parser.add_argument(
        "--batch-study-manifest",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Successful clip manifest used to hash each batch study set.",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def _successful_study_set(path: Path) -> set[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path)
    if "study_id" not in frame.columns:
        raise ValueError(f"Batch study manifest lacks study_id: {path.name}")
    if "write_ok" in frame.columns:
        values = frame["write_ok"]
        if pd.api.types.is_bool_dtype(values):
            keep = values.fillna(False).astype(bool)
        else:
            keep = values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
        frame = frame.loc[keep]
    studies = {str(value).strip() for value in frame["study_id"] if str(value).strip()}
    if not studies:
        raise ValueError(f"Batch study manifest contains no successful studies: {path.name}")
    return studies


def _selected_universe(path: Path) -> tuple[set[str], int]:
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path)
    missing = {"study_id", "subject_id"} - set(frame.columns)
    if missing:
        raise ValueError(f"Selected study universe missing columns: {sorted(missing)}")
    normalized = frame[["study_id", "subject_id"]].astype(str)
    if normalized.eq("").any().any():
        raise ValueError("Selected study universe contains empty identifiers")
    if normalized.groupby("study_id")["subject_id"].nunique().gt(1).any():
        raise ValueError("Selected study universe maps a study to multiple subjects")
    return set(normalized["study_id"]), int(normalized["subject_id"].nunique())


def build_declared_scope_metadata(
    batches: dict[str, dict[str, str]],
    outside_batches: set[str],
    manifests: dict[str, Path],
    selected_studies: set[str],
) -> None:
    if set(manifests) != set(batches):
        raise ValueError(
            "--batch-study-manifest names must exactly match --batch-source names"
        )
    study_sets = {name: _successful_study_set(path) for name, path in manifests.items()}
    fullscale_union = set().union(
        *(study_sets[name] for name, item in batches.items() if item["source_class"] == "fullscale")
    )
    for name, metadata in batches.items():
        studies = study_sets[name]
        metadata.update(
            {
                "successful_study_count": len(studies),
                "successful_study_set_sha256": canonical_id_set_sha256(studies),
                "source_manifest_sha256": sha256_file(manifests[name]),
            }
        )
        if name not in outside_batches:
            continue
        inside = studies & selected_studies
        outside = studies - selected_studies
        later_overlap = studies & fullscale_union
        canonical_contribution = inside - fullscale_union
        metadata["declared_legacy_scope"] = {
            "version": DECLARED_LEGACY_SCOPE_VERSION,
            "legacy_studies_total": len(studies),
            "legacy_study_set_sha256": canonical_id_set_sha256(studies),
            "inside_selected_universe_count": len(inside),
            "inside_selected_universe_study_set_sha256": canonical_id_set_sha256(inside),
            "outside_selected_universe_count": len(outside),
            "outside_selected_universe_study_set_sha256": canonical_id_set_sha256(outside),
            "later_fullscale_overlap_count": len(later_overlap),
            "later_fullscale_overlap_study_set_sha256": canonical_id_set_sha256(later_overlap),
            "deduplicated_canonical_contribution_count": len(canonical_contribution),
            "deduplicated_canonical_contribution_study_set_sha256": canonical_id_set_sha256(
                canonical_contribution
            ),
        }


def main() -> int:
    args = parse_args()
    if not args.split_map_csv.exists():
        raise FileNotFoundError(args.split_map_csv)
    batches = parse_name_class(args.batch_source)
    outside_batches = parse_outside_universe_batches(args.allow_outside_universe_batch, batches)
    for name, metadata in batches.items():
        metadata["outside_universe_policy"] = (
            "declared_legacy_scope" if name in outside_batches else "canonical_only"
        )
    selected_payload = None
    if outside_batches:
        if args.selected_studies_csv is None:
            raise ValueError("--selected-studies-csv is required for declared legacy scope")
        if not str(args.selected_universe_selection_rule or "").strip():
            raise ValueError(
                "--selected-universe-selection-rule is required for declared legacy scope"
            )
        manifests = parse_named_paths(args.batch_study_manifest, "batch study manifest")
        selected_studies, selected_subjects = _selected_universe(args.selected_studies_csv)
        build_declared_scope_metadata(
            batches,
            outside_batches,
            manifests,
            selected_studies,
        )
        selected_payload = {
            "study_count": len(selected_studies),
            "subject_count": selected_subjects,
            "study_set_sha256": canonical_id_set_sha256(selected_studies),
            "source_sha256": sha256_file(args.selected_studies_csv),
            "deterministic_selection_rule": str(args.selected_universe_selection_rule).strip(),
        }
    elif args.batch_study_manifest or args.selected_studies_csv is not None:
        raise ValueError(
            "Selected-universe and batch manifests are accepted only with declared legacy scope"
        )
    payload = {
        "protocol_version": "jdim-tier1-v1",
        "flow_structure": "parallel_branches",
        "mimic_iv_echo_release": args.mimic_iv_echo_release,
        "source_denominator_definition": args.source_denominator_definition,
        "imaging_lineage": args.imaging_lineage,
        "label_lineage": args.label_lineage,
        "split_map": {
            "version": args.split_version,
            "generator": args.split_generator,
            "expected_sha256": sha256_file(args.split_map_csv),
        },
        "batch_sources": batches,
        "allowed_batch_overlap_pairs": parse_overlap_pairs(args.allow_batch_overlap, set(batches)),
    }
    if selected_payload is not None:
        payload["selected_analysis_universe"] = selected_payload
    write_json(args.output_json, payload)
    print(json.dumps({"status": "ok", "output_json": str(args.output_json), "batch_count": len(batches)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
