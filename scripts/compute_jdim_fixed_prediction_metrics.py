#!/usr/bin/env python3
"""Compute reviewer metrics from frozen, unchanged row-level predictions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from jdim_tier1.metrics import (
    compute_fixed_prediction_metrics,
    validate_prediction_file_schemas,
    write_fixed_metric_outputs,
)
from jdim_tier1.safety import Tier1BlockedError, parse_named_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--imaging-predictions",
        action="append",
        default=[],
        metavar="TARGET=PATH",
        help="Frozen all-split imaging prediction CSV; provide lvot_vti and tapse.",
    )
    parser.add_argument(
        "--nonimage-predictions",
        action="append",
        default=[],
        metavar="COMPARATOR=PATH",
        help="Optional frozen row-level comparator prediction CSV.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-n", type=int, default=2000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260824)
    parser.add_argument(
        "--restricted-inputs-acknowledged",
        action="store_true",
        help="Confirm row-level predictions remain in approved restricted storage.",
    )
    parser.add_argument("--schema-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.restricted_inputs_acknowledged:
        print(json.dumps({"status": "BLOCKED_RESTRICTED_INPUT_ACKNOWLEDGEMENT"}, indent=2))
        return 2
    try:
        imaging_paths = parse_named_paths(args.imaging_predictions, "imaging prediction")
        if set(imaging_paths) != {"lvot_vti", "tapse"}:
            raise ValueError("Provide exactly lvot_vti=PATH and tapse=PATH imaging predictions")
        nonimage_paths = parse_named_paths(args.nonimage_predictions, "nonimage prediction")
        if args.schema_only:
            print(json.dumps(validate_prediction_file_schemas(imaging_paths, nonimage_paths), indent=2))
            return 0
        imaging = {target: pd.read_csv(path) for target, path in imaging_paths.items()}
        nonimage = {name: pd.read_csv(path) for name, path in nonimage_paths.items()}
        result = compute_fixed_prediction_metrics(
            imaging,
            imaging_sources=imaging_paths,
            nonimage_predictions=nonimage,
            nonimage_sources=nonimage_paths,
            n_bootstrap=args.bootstrap_n,
            seed=args.bootstrap_seed,
        )
        write_fixed_metric_outputs(result, args.output_dir)
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "status": "ok",
                "output_dir": str(args.output_dir),
                "model_refit": False,
                "prediction_regeneration": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
