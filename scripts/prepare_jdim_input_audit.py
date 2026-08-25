#!/usr/bin/env python3
"""Validate, sample, or aggregate the restricted JDIM input-content audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from jdim_tier1.audit import (
    aggregate_audit_annotations,
    build_adjudication_queue,
    build_audit_sample,
    derive_post_unblinding_value_matches,
    load_manual_audit_completion,
    load_audit_config,
    write_audit_aggregates,
    write_adjudication_queue,
    write_audit_sample,
    write_post_unblinding_value_matches,
)
from jdim_tier1.safety import Tier1BlockedError, parse_named_paths, sha256_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-config")
    validate.add_argument("--config", type=Path, required=True)
    validate.add_argument("--output-json", type=Path, default=None)

    validate_inputs = subparsers.add_parser("validate-inputs")
    validate_inputs.add_argument("--config", type=Path, required=True)
    validate_inputs.add_argument("--cohort", action="append", default=[], metavar="TARGET=PATH")
    validate_inputs.add_argument("--canonical-clip-manifest-csv", type=Path, required=True)

    for name in ("sample", "pilot"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--cohort", action="append", default=[], metavar="TARGET=PATH")
        command.add_argument("--canonical-clip-manifest-csv", type=Path, required=True)
        command.add_argument("--opaque-id-key-file", type=Path, required=True)
        command.add_argument("--restricted-output-root", type=Path, required=True)
        command.add_argument("--safe-output-dir", type=Path, required=True)
        command.add_argument("--sample-size-per-target", type=int, default=None)
        command.add_argument("--allocation-override-json", type=Path, default=None)
        if name == "pilot":
            command.add_argument("--pilot-n-per-target", type=int, default=None)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--config", type=Path, required=True)
    aggregate.add_argument("--study-annotations-csv", type=Path, required=True)
    aggregate.add_argument("--clip-annotations-csv", type=Path, required=True)
    aggregate.add_argument("--restricted-linkage-csv", type=Path, required=True)
    aggregate.add_argument("--restricted-sampling-design-csv", type=Path, required=True)
    aggregate.add_argument("--restricted-second-reader-manifest-csv", type=Path, required=True)
    aggregate.add_argument("--restricted-clip-roster-csv", type=Path, required=True)
    aggregate.add_argument("--completed-adjudication-csv", type=Path, required=True)
    aggregate.add_argument("--safe-output-dir", type=Path, required=True)
    aggregate.add_argument("--bootstrap-n", type=int, default=2000)

    adjudication = subparsers.add_parser("adjudication-queue")
    adjudication.add_argument("--config", type=Path, required=True)
    adjudication.add_argument("--study-annotations-csv", type=Path, required=True)
    adjudication.add_argument("--clip-annotations-csv", type=Path, required=True)
    adjudication.add_argument("--restricted-output-csv", type=Path, required=True)

    match = subparsers.add_parser("post-unblinding-match")
    match.add_argument("--config", type=Path, required=True)
    match.add_argument("--clip-annotations-csv", type=Path, required=True)
    match.add_argument("--restricted-linkage-csv", type=Path, required=True)
    match.add_argument("--restricted-sampling-design-csv", type=Path, required=True)
    match.add_argument("--restricted-clip-roster-csv", type=Path, required=True)
    match.add_argument("--completed-adjudication-csv", type=Path, required=True)
    match.add_argument("--manual-audit-completion-json", type=Path, required=True)
    match.add_argument("--restricted-output-csv", type=Path, required=True)
    match.add_argument("--safe-output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config, config_hash = load_audit_config(args.config)
        if args.command == "validate-config":
            payload = {
                "status": "ok",
                "protocol_version": config["protocol_version"],
                "configuration_sha256": config_hash,
            }
            if args.output_json is not None:
                args.output_json.parent.mkdir(parents=True, exist_ok=True)
                args.output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(payload, indent=2))
            return 0
        if args.command == "validate-inputs":
            cohort_paths = parse_named_paths(args.cohort, "audit cohort")
            if set(cohort_paths) != set(config["target_cohorts"]):
                raise ValueError("Audit cohort roles must exactly match configured targets")
            for target, path in cohort_paths.items():
                header = pd.read_csv(path, nrows=0)
                missing = {"study_id", "subject_id", "split"} - set(header.columns)
                if missing:
                    raise ValueError(f"{target} cohort schema missing {sorted(missing)}")
            clip_header = pd.read_csv(args.canonical_clip_manifest_csv, nrows=0)
            clip_missing = {"study_id", "subject_id"} - set(clip_header.columns)
            if clip_missing or not {
                "canonical_clip_id",
                "embedding_idx",
                "source_embedding_idx",
            }.intersection(clip_header.columns):
                raise ValueError(
                    "canonical clip manifest must contain study/subject IDs and a stable clip index"
                )
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "mode": "schema_only",
                        "configuration_sha256": config_hash,
                        "target_cohorts_checked": sorted(cohort_paths),
                        "sampling_executed": False,
                    },
                    indent=2,
                )
            )
            return 0
        if args.command in {"sample", "pilot"}:
            cohort_paths = parse_named_paths(args.cohort, "audit cohort")
            cohorts = {target: pd.read_csv(path) for target, path in cohort_paths.items()}
            canonical_clips = pd.read_csv(args.canonical_clip_manifest_csv)
            if not args.opaque_id_key_file.exists():
                raise FileNotFoundError("Opaque audit ID key file is missing")
            key = args.opaque_id_key_file.read_bytes()
            if len(key) < 16:
                raise ValueError("Opaque audit ID key must contain at least 16 bytes")
            override = None
            if args.allocation_override_json is not None:
                override = json.loads(args.allocation_override_json.read_text(encoding="utf-8"))
            result = build_audit_sample(
                cohorts,
                canonical_clips,
                config,
                config_hash,
                key,
                sample_size_per_target=args.sample_size_per_target,
                allocation_override=override,
                technical_pilot=args.command == "pilot",
                pilot_n_per_target=getattr(args, "pilot_n_per_target", None),
            )
            write_audit_sample(result, args.restricted_output_root, args.safe_output_dir)
            print(json.dumps({"status": "ok", **result.safe_summary}, indent=2))
            return 0

        if args.command == "adjudication-queue":
            study = pd.read_csv(args.study_annotations_csv)
            try:
                clip = pd.read_csv(args.clip_annotations_csv)
            except pd.errors.EmptyDataError:
                clip = pd.DataFrame()
            queue = build_adjudication_queue(study, clip)
            write_adjudication_queue(queue, args.restricted_output_csv)
            print(json.dumps({"status": "ok", "n_adjudication_rows": int(len(queue))}, indent=2))
            return 0

        if args.command == "post-unblinding-match":
            clip = pd.read_csv(args.clip_annotations_csv)
            linkage = pd.read_csv(args.restricted_linkage_csv)
            design = pd.read_csv(args.restricted_sampling_design_csv)
            clip_roster = pd.read_csv(args.restricted_clip_roster_csv)
            adjudication_queue = pd.read_csv(args.completed_adjudication_csv)
            audit_completion = load_manual_audit_completion(args.manual_audit_completion_json)
            input_hashes = {
                "clip_annotations": sha256_file(args.clip_annotations_csv),
                "audit_linkage": sha256_file(args.restricted_linkage_csv),
                "sampling_design": sha256_file(args.restricted_sampling_design_csv),
                "clip_roster": sha256_file(args.restricted_clip_roster_csv),
                "completed_adjudication": sha256_file(args.completed_adjudication_csv),
                "manual_audit_completion": sha256_file(args.manual_audit_completion_json),
            }
            result = derive_post_unblinding_value_matches(
                clip,
                linkage,
                design,
                clip_roster,
                adjudication_queue,
                config,
                config_hash,
                audit_completion,
                input_hashes,
            )
            write_post_unblinding_value_matches(
                result,
                args.restricted_output_csv,
                args.safe_output_dir,
            )
            print(json.dumps({"status": "ok", "n_targets": int(len(result.safe_summary))}, indent=2))
            return 0

        study = pd.read_csv(args.study_annotations_csv)
        try:
            clip = pd.read_csv(args.clip_annotations_csv)
        except pd.errors.EmptyDataError:
            clip = pd.DataFrame()
        linkage = pd.read_csv(args.restricted_linkage_csv)
        design = pd.read_csv(args.restricted_sampling_design_csv)
        second_reader_manifest = pd.read_csv(args.restricted_second_reader_manifest_csv)
        clip_roster = pd.read_csv(args.restricted_clip_roster_csv)
        adjudication_queue = pd.read_csv(args.completed_adjudication_csv)
        input_hashes = {
            "study_annotations": sha256_file(args.study_annotations_csv),
            "clip_annotations": sha256_file(args.clip_annotations_csv),
            "audit_linkage": sha256_file(args.restricted_linkage_csv),
            "sampling_design": sha256_file(args.restricted_sampling_design_csv),
            "second_reader_manifest": sha256_file(args.restricted_second_reader_manifest_csv),
            "clip_roster": sha256_file(args.restricted_clip_roster_csv),
            "completed_adjudication": sha256_file(args.completed_adjudication_csv),
        }
        result = aggregate_audit_annotations(
            study,
            clip,
            linkage,
            design,
            config,
            config_hash,
            second_reader_manifest,
            clip_roster,
            adjudication_queue,
            input_hashes=input_hashes,
            n_bootstrap=args.bootstrap_n,
        )
        write_audit_aggregates(result, args.safe_output_dir)
        print(json.dumps({"status": "ok", **result.summary}, indent=2))
        return 0
    except Tier1BlockedError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
