#!/usr/bin/env python3
"""Classify repeated JDIM clip keys from restricted frozen artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.duplicate_forensics import (
    BatchArtifacts,
    DuplicateForensicsInputs,
    analyze_duplicate_keys,
    write_duplicate_forensics_outputs,
)
from jdim_tier1.safety import Tier1BlockedError, parse_named_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--extraction-manifest",
        action="append",
        default=[],
        metavar="BATCH=PATH",
        help="Successful extraction manifest for one batch; repeat by batch.",
    )
    parser.add_argument(
        "--embedding-manifest",
        action="append",
        default=[],
        metavar="BATCH=PATH",
        help="Clip embedding manifest for one batch; repeat by batch.",
    )
    parser.add_argument(
        "--embedding-npz",
        action="append",
        default=[],
        metavar="BATCH=PATH",
        help="Embedding array NPZ paired with one batch; repeat by batch.",
    )
    parser.add_argument("--subject-split-map-csv", type=Path, required=True)
    parser.add_argument(
        "--target-cohort",
        action="append",
        default=[],
        metavar="TARGET=PATH",
        help="Study-membership CSV; provide lvot_vti and tapse.",
    )
    parser.add_argument(
        "--coarse-key-column",
        action="append",
        default=[],
        help="Column in the repeated coarse key; default: study_id and dicom_filepath.",
    )
    parser.add_argument("--near-rtol", type=float, default=1e-6)
    parser.add_argument("--near-atol", type=float, default=1e-7)
    parser.add_argument("--restricted-output-dir", type=Path, required=True)
    parser.add_argument("--safe-output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        extraction = parse_named_paths(args.extraction_manifest, "extraction manifest")
        manifests = parse_named_paths(args.embedding_manifest, "embedding manifest")
        arrays = parse_named_paths(args.embedding_npz, "embedding NPZ")
        if not extraction or set(extraction) != set(manifests) or set(extraction) != set(arrays):
            raise ValueError(
                "--extraction-manifest, --embedding-manifest, and --embedding-npz "
                "must provide the same nonempty batch-name set"
            )
        targets = parse_named_paths(args.target_cohort, "target cohort")
        batches = {
            name: BatchArtifacts(
                extraction_manifest=extraction[name],
                embedding_manifest=manifests[name],
                embedding_npz=arrays[name],
            )
            for name in sorted(extraction)
        }
        result = analyze_duplicate_keys(
            DuplicateForensicsInputs(
                batches=batches,
                split_map=args.subject_split_map_csv,
                target_cohorts=targets,
                coarse_key_columns=(
                    tuple(args.coarse_key_column)
                    if args.coarse_key_column
                    else ("study_id", "dicom_filepath")
                ),
                near_rtol=args.near_rtol,
                near_atol=args.near_atol,
            )
        )
        write_duplicate_forensics_outputs(
            result,
            restricted_output_dir=args.restricted_output_dir,
            safe_output_dir=args.safe_output_dir,
            worktree=Path(__file__).resolve().parents[1],
        )
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED_INVALID_FORENSIC_INPUT", "detail": str(exc)}, indent=2))
        return 2

    print(
        json.dumps(
            {
                "status": result.status,
                "n_repeated_coarse_groups": result.summary["n_repeated_coarse_groups"],
                "classification_counts": result.summary["classification_counts"],
                "n_studies_whose_embedding_changes_under_valid_rule": result.summary[
                    "n_studies_whose_embedding_changes_under_valid_rule"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
