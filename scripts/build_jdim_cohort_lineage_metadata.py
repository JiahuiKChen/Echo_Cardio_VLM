#!/usr/bin/env python3
"""Build path-free pinned lineage metadata for JDIM cohort reconstruction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.safety import SAFE_ROLE_PATTERN, sha256_file, write_json


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
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.split_map_csv.exists():
        raise FileNotFoundError(args.split_map_csv)
    batches = parse_name_class(args.batch_source)
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
    write_json(args.output_json, payload)
    print(json.dumps({"status": "ok", "output_json": str(args.output_json), "batch_count": len(batches)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
