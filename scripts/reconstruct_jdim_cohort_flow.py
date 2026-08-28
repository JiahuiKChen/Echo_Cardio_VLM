#!/usr/bin/env python3
"""Reconstruct aggregate JDIM cohort flow from pinned restricted artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.cohort_flow import (
    CohortFlowInputs,
    reconstruct_cohort_flow,
    validate_cohort_input_schemas,
    validate_cohort_output_generation,
    write_cohort_flow_outputs,
)
from jdim_tier1.safety import Tier1BlockedError, parse_named_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-studies-csv", type=Path, required=True)
    parser.add_argument("--eligible-studies-csv", type=Path, required=True)
    parser.add_argument("--expected-records-csv", type=Path, nargs="+", required=True)
    parser.add_argument("--dicom-audit-csv", type=Path, nargs="+", required=True)
    parser.add_argument("--extraction-manifest-csv", type=Path, nargs="+", required=True)
    parser.add_argument(
        "--embedding-batch",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Named successful clip-embedding manifest; repeat for each legacy/full-scale batch.",
    )
    parser.add_argument(
        "--embedding-batch-npz",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Named frozen clip-embedding array paired with --embedding-batch.",
    )
    parser.add_argument("--final-study-embedding-manifest-csv", type=Path, required=True)
    parser.add_argument("--structured-measurements-csv", type=Path, required=True)
    parser.add_argument("--subject-split-map-csv", type=Path, required=True)
    parser.add_argument(
        "--canonical-summary",
        action="append",
        default=[],
        metavar="TARGET=PATH",
        help="Pinned primary imaging-baseline summary for lvot_vti and tapse.",
    )
    parser.add_argument("--lineage-metadata-json", type=Path, required=True)
    parser.add_argument("--duplicate-forensics-rows-csv", type=Path, required=True)
    parser.add_argument("--duplicate-forensics-provenance-json", type=Path, required=True)
    parser.add_argument("--duplicate-metadata-summary-json", type=Path, required=True)
    parser.add_argument("--duplicate-metadata-groups-csv", type=Path, required=True)
    parser.add_argument("--duplicate-metadata-stage-rows-csv", type=Path, required=True)
    parser.add_argument("--corrected-clip-embedding-manifest-csv", type=Path, required=True)
    parser.add_argument(
        "--canonical-prediction",
        action="append",
        default=[],
        metavar="TARGET=PATH",
        help="Pinned restricted prediction rows for exact target/cohort reconciliation.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restricted-reconciliation-csv", type=Path, default=None)
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Validate file headers, declared lineage, and pinned hashes without computing cohort counts.",
    )
    parser.add_argument(
        "--no-write-preflight",
        action="store_true",
        help="Run the complete cohort and output-validation path without writing artifacts.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        embedding_batches = parse_named_paths(args.embedding_batch, "embedding batch")
        embedding_batch_npzs = parse_named_paths(
            args.embedding_batch_npz, "embedding batch NPZ"
        )
        canonical_summaries = parse_named_paths(args.canonical_summary, "canonical summary")
        canonical_predictions = parse_named_paths(args.canonical_prediction, "canonical prediction")
        if set(canonical_summaries) != {"lvot_vti", "tapse"}:
            raise ValueError("--canonical-summary must provide lvot_vti=PATH and tapse=PATH")
        if set(canonical_predictions) != {"lvot_vti", "tapse"}:
            raise ValueError("--canonical-prediction must provide lvot_vti=PATH and tapse=PATH")
        inputs = CohortFlowInputs(
            source_studies=args.source_studies_csv,
            eligible_studies=args.eligible_studies_csv,
            expected_records=args.expected_records_csv,
            dicom_audits=args.dicom_audit_csv,
            extraction_manifests=args.extraction_manifest_csv,
            embedding_batches=embedding_batches,
            embedding_batch_npzs=embedding_batch_npzs,
            final_study_embeddings=args.final_study_embedding_manifest_csv,
            structured_measurements=args.structured_measurements_csv,
            split_map=args.subject_split_map_csv,
            canonical_summaries=canonical_summaries,
            lineage_metadata=args.lineage_metadata_json,
            duplicate_forensics=args.duplicate_forensics_rows_csv,
            duplicate_forensics_provenance=args.duplicate_forensics_provenance_json,
            canonical_predictions=canonical_predictions,
            duplicate_metadata_summary=args.duplicate_metadata_summary_json,
            duplicate_metadata_groups=args.duplicate_metadata_groups_csv,
            duplicate_metadata_stage_rows=args.duplicate_metadata_stage_rows_csv,
            corrected_clip_embeddings=args.corrected_clip_embedding_manifest_csv,
        )
        if args.schema_only:
            print(json.dumps(validate_cohort_input_schemas(inputs), indent=2))
            return 0
        result = reconstruct_cohort_flow(inputs)
        if args.no_write_preflight:
            print(json.dumps(validate_cohort_output_generation(result), indent=2))
            return 0
        write_cohort_flow_outputs(result, args.output_dir, args.restricted_reconciliation_csv)
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    print(json.dumps({"status": "ok", "output_dir": str(args.output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
