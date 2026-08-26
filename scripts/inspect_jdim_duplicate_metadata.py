#!/usr/bin/env python3
"""Trace exact repeated JDIM clip keys through text manifests only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jdim_tier1.duplicate_metadata import (
    MetadataInspectionInputs,
    MetadataStage,
    STAGE_ORDER,
    inspect_duplicate_metadata,
    parse_path_remaps,
    write_metadata_inspection_outputs,
)
from jdim_tier1.safety import Tier1BlockedError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-forensics-rows", type=Path, required=True)
    parser.add_argument("--expected-records-csv", type=Path, required=True)
    parser.add_argument("--dicom-audit-csv", type=Path, required=True)
    parser.add_argument("--cine-candidates-csv", type=Path, required=True)
    parser.add_argument("--extraction-manifest-csv", type=Path, required=True)
    parser.add_argument("--batch-embedding-manifest-csv", type=Path, required=True)
    parser.add_argument("--merged-embedding-manifest-csv", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--source-root",
        action="append",
        default=[],
        type=Path,
        help="Declared exact DICOM root; only root plus affected relative paths is checked.",
    )
    parser.add_argument(
        "--npz-candidate-root",
        action="append",
        default=[],
        type=Path,
        help="Declared nonrecursive root checked by exact affected NPZ basename only.",
    )
    parser.add_argument(
        "--path-remap",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="Declared historical exact-prefix migration; repeat as needed.",
    )
    parser.add_argument("--preprocessing-git-sha", default="")
    parser.add_argument("--preprocessing-script", type=Path)
    parser.add_argument("--embedding-script", type=Path)
    parser.add_argument("--runner-script", type=Path)
    parser.add_argument("--encoder-checkpoint-path", type=Path)
    parser.add_argument(
        "--encoder-checkpoint-sha256",
        default="",
        help="Previously established checkpoint hash; this command never hashes the checkpoint.",
    )
    parser.add_argument("--target-frames", type=int, default=32)
    parser.add_argument("--target-size", type=int, default=224)
    parser.add_argument("--encoder-frames", type=int, default=16)
    parser.add_argument("--temporal-stride", type=int, default=2)
    parser.add_argument("--expected-group-count", type=int, default=32)
    parser.add_argument("--expected-row-count", type=int, default=64)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stages = (
        MetadataStage("expected_records", args.expected_records_csv),
        MetadataStage("dicom_audit", args.dicom_audit_csv),
        MetadataStage("cine_candidates", args.cine_candidates_csv),
        MetadataStage("extraction_manifest", args.extraction_manifest_csv),
        MetadataStage("batch_embedding_manifest", args.batch_embedding_manifest_csv),
        MetadataStage("merged_embedding_manifest", args.merged_embedding_manifest_csv),
    )
    assert tuple(stage.name for stage in stages) == STAGE_ORDER
    try:
        result = inspect_duplicate_metadata(
            MetadataInspectionInputs(
                prior_forensics_rows=args.prior_forensics_rows,
                stages=stages,
                output_root=args.output_root,
                source_roots=tuple(args.source_root),
                npz_candidate_roots=tuple(args.npz_candidate_root),
                path_remaps=parse_path_remaps(args.path_remap),
                preprocessing_git_sha=args.preprocessing_git_sha,
                preprocessing_script=args.preprocessing_script,
                embedding_script=args.embedding_script,
                runner_script=args.runner_script,
                encoder_checkpoint_path=args.encoder_checkpoint_path,
                encoder_checkpoint_sha256=args.encoder_checkpoint_sha256,
                target_frames=args.target_frames,
                target_size=args.target_size,
                encoder_frames=args.encoder_frames,
                temporal_stride=args.temporal_stride,
                expected_group_count=args.expected_group_count,
                expected_row_count=args.expected_row_count,
            )
        )
        write_metadata_inspection_outputs(
            result,
            args.output_root,
            worktree=Path(__file__).resolve().parents[1],
        )
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2
    except (FileExistsError, FileNotFoundError, KeyError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED_DUPLICATE_METADATA_INPUT", "detail": str(exc)},
                indent=2,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "status": result.status,
                "n_groups": result.summary["n_groups"],
                "first_duplicate_stage_counts": result.summary[
                    "first_duplicate_stage_counts"
                ],
                "classification_counts": result.summary["classification_counts"],
                "n_groups_requiring_rehydration": result.summary[
                    "n_groups_requiring_rehydration"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

