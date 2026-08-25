#!/usr/bin/env python3
"""Build immutable corrected clip and mean-pooled study embeddings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.corrected_analysis import (
    build_corrected_aggregation,
    write_corrected_aggregation,
)
from jdim_tier1.safety import BLOCKED_LINEAGE, Tier1BlockedError, sha256_file


def validate_forensic_evidence_hash(evidence: Path, provenance: Path) -> None:
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    expected = payload.get("restricted_artifact_sha256", {}).get(
        "duplicate_forensics_rows.csv"
    )
    observed = sha256_file(evidence)
    if payload.get("status") != "ok" or not expected or expected != observed:
        raise Tier1BlockedError(
            BLOCKED_LINEAGE,
            "corrected aggregation forensic evidence does not match resolved provenance",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--forensic-evidence-file",
        dest="forensic_evidence_file",
        type=Path,
        required=True,
        help=(
            "Restricted duplicate_forensics_rows.csv from the approved forensic tool."
        ),
    )
    parser.add_argument(
        "--clip-manifest-csv",
        type=Path,
        required=True,
        help="Restricted original clip embedding manifest.",
    )
    parser.add_argument(
        "--forensic-provenance-json",
        type=Path,
        required=True,
        help="Aggregate-safe duplicate_forensics_summary.json that hashes the restricted evidence.",
    )
    parser.add_argument(
        "--clip-embedding-npz",
        type=Path,
        required=True,
        help="Restricted original clip embedding NPZ.",
    )
    parser.add_argument(
        "--frozen-study-manifest-csv",
        type=Path,
        required=True,
        help="Restricted frozen Phase 2 study embedding manifest.",
    )
    parser.add_argument(
        "--frozen-study-embedding-npz",
        type=Path,
        required=True,
        help="Restricted frozen Phase 2 study embedding NPZ.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="New absolute restricted output root; an existing path is refused.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_forensic_evidence_hash(
        args.forensic_evidence_file,
        args.forensic_provenance_json,
    )
    result = build_corrected_aggregation(
        forensic_evidence_file=args.forensic_evidence_file,
        clip_manifest_csv=args.clip_manifest_csv,
        clip_embedding_npz=args.clip_embedding_npz,
        frozen_study_manifest_csv=args.frozen_study_manifest_csv,
        frozen_study_embedding_npz=args.frozen_study_embedding_npz,
    )
    destination = write_corrected_aggregation(result, args.output_root)
    summary = result.aggregate_change_counts.iloc[0].to_dict()
    summary["output_created"] = True
    summary["output_root_name"] = destination.name
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
