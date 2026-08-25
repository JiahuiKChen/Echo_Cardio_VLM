from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from jdim_tier1.corrected_completion import (  # noqa: E402
    AGGREGATION_INPUT_KEYS,
    AGGREGATION_OUTPUT_FILES,
    COMPARISON_BASELINE_TABLES,
    COMPARISON_OUTPUT_FILES,
    COMPARISON_REVIEWER_TABLES,
    TARGETS,
    required_corrected_artifacts,
)
from jdim_tier1.safety import safe_file_record, sha256_file  # noqa: E402


def build_valid_corrected_fixture(
    corrected_root: Path,
    source_root: Path,
    aggregation_parents: dict[str, Path] | None = None,
) -> dict[str, Path]:
    """Create a complete synthetic corrected tree with cryptographically bound provenance."""

    artifacts = required_corrected_artifacts(corrected_root)
    for path in artifacts.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.name not in {
            "corrected_aggregation_provenance_restricted.json",
            "original_vs_corrected_comparison_provenance.json",
        }:
            path.write_bytes(b"synthetic artifact\n")

    supplied = aggregation_parents or {}
    key_to_supplied_role = {
        "clip_manifest": "historical_clip_embedding_manifest",
        "clip_embeddings": "historical_clip_embedding_array",
        "frozen_study_manifest": "frozen_study_embedding_manifest",
        "frozen_study_embeddings": "frozen_study_embedding_array",
    }
    parents: dict[str, Path] = {}
    for role in sorted(AGGREGATION_INPUT_KEYS):
        supplied_role = key_to_supplied_role.get(role)
        parent = supplied.get(supplied_role) if supplied_role else None
        if parent is None:
            parent = source_root / "aggregation_parents" / f"{role}.bin"
            parent.parent.mkdir(parents=True, exist_ok=True)
            parent.write_bytes(f"synthetic {role}\n".encode("ascii"))
        elif not parent.is_file():
            raise FileNotFoundError(parent)
        parents[role] = parent

    aggregation = {
        "schema_version": "jdim-corrected-aggregation-v1",
        "status": "CORRECTED_AGGREGATION_BUILT",
        "canonical_identity_target_prediction_independent": True,
        "frozen_parent_replay_exact": True,
        "frozen_parent_clip_counts_exact": True,
        "original_inputs_preserved": True,
        "input_files": {
            role: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for role, path in parents.items()
        },
        "output_files": [
            safe_file_record(role, corrected_root / relative)
            for role, relative in sorted(AGGREGATION_OUTPUT_FILES.items())
        ],
    }
    aggregation_path = (
        corrected_root
        / "aggregation/restricted/corrected_aggregation_provenance_restricted.json"
    )
    aggregation_path.write_text(json.dumps(aggregation), encoding="utf-8")

    split = source_root / "frozen_split_map.csv"
    split.parent.mkdir(parents=True, exist_ok=True)
    split.write_text("subject_id,split\n1,test\n", encoding="utf-8")
    for comparison_name, scope, include_reviewer in (
        ("original_vs_corrected_main", "all_clips", True),
        (
            "original_vs_corrected_hard_extremes",
            "all_clips_exclude_hard_extremes",
            False,
        ),
    ):
        records = [safe_file_record("frozen_split_map", split)]
        for target in TARGETS:
            analysis = corrected_root / "restricted" / "analyses" / target / scope
            corrected_sources = {
                f"corrected_predictions_{target}": analysis
                / "imaging_baseline_predictions.csv",
                f"corrected_summary_{target}": analysis / "imaging_baseline_summary.json",
            }
            records.extend(
                safe_file_record(role, source)
                for role, source in corrected_sources.items()
            )
            for label in ("predictions", "summary"):
                role = f"original_{label}_{target}"
                source = source_root / comparison_name / role
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(f"synthetic {role}\n".encode("ascii"))
                records.append(safe_file_record(role, source))
            for filename in COMPARISON_BASELINE_TABLES:
                corrected_role = f"corrected_{target}_{filename}"
                records.append(safe_file_record(corrected_role, analysis / filename))
                original_role = f"original_{target}_{filename}"
                source = source_root / comparison_name / original_role
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(f"synthetic {original_role}\n".encode("ascii"))
                records.append(safe_file_record(original_role, source))
        if include_reviewer:
            reviewer_root = corrected_root / "aggregate_safe" / "reviewer_metrics"
            for filename in COMPARISON_REVIEWER_TABLES:
                corrected_role = f"corrected_reviewer_metrics_{filename}"
                records.append(safe_file_record(corrected_role, reviewer_root / filename))
                original_role = f"original_reviewer_metrics_{filename}"
                source = source_root / comparison_name / original_role
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(f"synthetic {original_role}\n".encode("ascii"))
                records.append(safe_file_record(original_role, source))

        directory = corrected_root / "aggregate_safe" / comparison_name
        comparison = {
            "schema_version": "jdim-original-corrected-comparison-v1",
            "status": "ORIGINAL_CORRECTED_IDENTITY_VERIFIED",
            "row_identity_verified": True,
            "study_subject_mapping_verified": True,
            "frozen_split_verified": True,
            "observed_report_label_identity_verified": True,
            "null_prediction_identity_verified": True,
            "run_protocol_verified": True,
            "targets": list(TARGETS),
            "input_files": records,
            "output_files": [
                safe_file_record(role, directory / filename)
                for role, filename in sorted(COMPARISON_OUTPUT_FILES.items())
            ],
        }
        (directory / "original_vs_corrected_comparison_provenance.json").write_text(
            json.dumps(comparison), encoding="utf-8"
        )
    return artifacts
