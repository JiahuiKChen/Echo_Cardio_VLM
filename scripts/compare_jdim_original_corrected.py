#!/usr/bin/env python3
"""Compare original and duplicate-corrected analyses without row-level export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from jdim_tier1.corrected_analysis import (
    compare_original_corrected,
    write_original_corrected_comparison,
)
from jdim_tier1.safety import (
    parse_named_paths,
    require_restricted_destination,
    safe_file_record,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--original-predictions",
        action="append",
        required=True,
        metavar="TARGET=PATH",
        help="Restricted original prediction CSV; repeat once per target.",
    )
    parser.add_argument(
        "--corrected-predictions",
        action="append",
        required=True,
        metavar="TARGET=PATH",
        help="Restricted corrected prediction CSV; repeat once per target.",
    )
    parser.add_argument(
        "--original-results",
        action="append",
        required=True,
        metavar="SCOPE=DIR",
        help="Original aggregate result directory; repeat once per target/scope.",
    )
    parser.add_argument(
        "--corrected-results",
        action="append",
        required=True,
        metavar="SCOPE=DIR",
        help="Corrected aggregate result directory; repeat once per target/scope.",
    )
    parser.add_argument(
        "--original-summary",
        action="append",
        required=True,
        metavar="TARGET=PATH",
        help="Restricted original imaging_baseline_summary.json; repeat once per target.",
    )
    parser.add_argument(
        "--corrected-summary",
        action="append",
        required=True,
        metavar="TARGET=PATH",
        help="Restricted corrected imaging_baseline_summary.json; repeat once per target.",
    )
    parser.add_argument("--frozen-split-map-csv", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="New aggregate-safe output root; an existing path is refused.",
    )
    parser.add_argument(
        "--metric-tolerance",
        type=float,
        default=1e-6,
        help="Absolute tolerance for aggregate-to-prediction metric reconciliation.",
    )
    return parser.parse_args()


def _load_named_csvs(paths: dict[str, Path], label: str) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    frames: dict[str, pd.DataFrame] = {}
    records: list[dict] = []
    for target, path in paths.items():
        require_restricted_destination(path)
        if not path.exists():
            raise FileNotFoundError(f"missing {label} for {target}: {path}")
        frame = pd.read_csv(path)
        frames[target] = frame
        records.append(safe_file_record(f"{label}_{target}", path, len(frame)))
    return frames, records


def _load_named_jsons(paths: dict[str, Path], label: str) -> tuple[dict[str, dict], list[dict]]:
    payloads: dict[str, dict] = {}
    records: list[dict] = []
    for target, path in paths.items():
        require_restricted_destination(path)
        if not path.exists():
            raise FileNotFoundError(f"missing {label} for {target}: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{label} for {target} must be a JSON object")
        payloads[target] = payload
        records.append(safe_file_record(f"{label}_{target}", path))
    return payloads, records


def main() -> int:
    args = parse_args()
    original_prediction_paths = parse_named_paths(
        args.original_predictions, "original prediction"
    )
    corrected_prediction_paths = parse_named_paths(
        args.corrected_predictions, "corrected prediction"
    )
    original_result_dirs = parse_named_paths(args.original_results, "original result")
    corrected_result_dirs = parse_named_paths(args.corrected_results, "corrected result")
    original_summary_paths = parse_named_paths(args.original_summary, "original summary")
    corrected_summary_paths = parse_named_paths(args.corrected_summary, "corrected summary")

    original_predictions, original_records = _load_named_csvs(
        original_prediction_paths, "original_predictions"
    )
    corrected_predictions, corrected_records = _load_named_csvs(
        corrected_prediction_paths, "corrected_predictions"
    )
    original_summaries, original_summary_records = _load_named_jsons(
        original_summary_paths, "original_summary"
    )
    corrected_summaries, corrected_summary_records = _load_named_jsons(
        corrected_summary_paths, "corrected_summary"
    )
    require_restricted_destination(args.frozen_split_map_csv)
    if not args.frozen_split_map_csv.exists():
        raise FileNotFoundError(f"missing frozen split map: {args.frozen_split_map_csv}")
    split_map = pd.read_csv(args.frozen_split_map_csv)
    input_records = [
        *original_records,
        *corrected_records,
        *original_summary_records,
        *corrected_summary_records,
        safe_file_record("frozen_split_map", args.frozen_split_map_csv, len(split_map)),
    ]

    result = compare_original_corrected(
        original_prediction_frames=original_predictions,
        corrected_prediction_frames=corrected_predictions,
        frozen_split_map=split_map,
        original_aggregate_dirs=original_result_dirs,
        corrected_aggregate_dirs=corrected_result_dirs,
        original_run_summaries=original_summaries,
        corrected_run_summaries=corrected_summaries,
        metric_tolerance=args.metric_tolerance,
        input_records=input_records,
    )
    destination = write_original_corrected_comparison(result, args.output_root)
    print(
        json.dumps(
            {
                "status": result.safe_provenance["status"],
                "targets": result.safe_provenance["targets"],
                "row_identity_verified": True,
                "frozen_split_verified": True,
                "output_root_name": destination.name,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
