#!/usr/bin/env python3
"""Validate completed C3 resource-preflight stages before a safe resume/skip."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence

import yaml

import audit_lvef_c3_storage as storage_audit
from lvef_multitask_analysis_modes import load_policy as load_safe_export_policy
import plan_lvef_c3_resources as resource_planner
import preflight_lvef_c3_full_source as source_preflight


class StageValidationError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise StageValidationError("REQUIRED_STAGE_JSON_MISSING")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise StageValidationError("STAGE_JSON_NOT_MAPPING")
    return value


def read_regular_file_bytes_no_follow(path: Path) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise StageValidationError("NOFOLLOW_FILE_OPEN_UNAVAILABLE")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StageValidationError("STAGE_FILE_NOT_REGULAR")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def validate_storage(run_root: Path, *, safe_export_policy: Path) -> None:
    detail = load_json(run_root / "restricted" / "scc_storage_inventory.restricted.json")
    safe_path = run_root / "aggregate" / storage_audit.STORAGE_SUMMARY_FILENAME
    policy, _ = load_safe_export_policy(safe_export_policy)
    safe_payload = read_regular_file_bytes_no_follow(safe_path)
    safe = storage_audit.validate_aggregate_summary_bytes(
        safe_payload,
        safe_export_policy=policy,
    )
    for value in (detail, safe):
        if value.get("status") != "PASS_READ_ONLY":
            raise StageValidationError("STORAGE_STAGE_NOT_PASS")
        if value.get("files_moved") != 0 or value.get("files_deleted") != 0:
            raise StageValidationError("STORAGE_STAGE_MUTATION_REPORTED")


def validate_source(
    run_root: Path,
    *,
    source_manifest: Path,
    selected_studies: Path,
    split_map: Path,
) -> None:
    aggregate = run_root / "aggregate"
    restricted = run_root / "restricted" / "source_preflight"
    summary = load_json(aggregate / "c3_full_source_preflight.summary.json")
    safety = load_json(aggregate / "c3_full_source_preflight_safety_gate.json")
    required_regular = (
        aggregate / "c3_full_source_preflight_by_batch.csv",
        restricted / "c3_full_source_object_metadata.restricted.jsonl",
        restricted / "c3_full_source_discrepancies.restricted.jsonl",
    )
    if any(path.is_symlink() or not path.is_file() for path in required_regular):
        raise StageValidationError("SOURCE_STAGE_OUTPUT_SET_INCOMPLETE")
    expected_zero = (
        "missing_objects",
        "unexpected_selected_objects",
        "changed_objects_relative_to_historical_metadata",
        "repeated_locator_groups_in_frozen_manifest",
        "ownership_conflicts",
        "zero_record_studies",
        "media_requests",
        "object_body_bytes_read",
    )
    if (
        summary.get("status") != "PASS_METADATA_ONLY"
        or summary.get("metadata_provider") != "GCS_JSON_API_OBJECTS_LIST"
        or summary.get("requested_objects") != 335984
        or summary.get("verified_objects") != 335984
        or summary.get("selected_studies") != 4530
        or summary.get("selected_subjects") != 4530
        or summary.get("raw_source_request_rows") != 336016
        or summary.get("historical_identical_rows_collapsed_before_frozen_manifest") != 32
        or summary.get("production_batches") != 19
        or summary.get("exact_source_bytes") != 1216569133322
        or summary.get("split_counts") != {"test": 680, "train": 3171, "val": 679}
        or any(summary.get(name) != 0 for name in expected_zero)
        or summary.get("dicom_bodies_downloaded") is not False
        or summary.get("selected_source_manifest_sha256") != sha256_file(source_manifest)
        or summary.get("selected_manifest_sha256") != sha256_file(selected_studies)
        or summary.get("split_manifest_sha256") != sha256_file(split_map)
    ):
        raise StageValidationError("SOURCE_INVENTORY_STAGE_NOT_AUTHORITATIVE_PASS")
    if (
        safety.get("status") != "PASS"
        or safety.get("safety_gate_passed") is not True
        or safety.get("media_requests") != 0
        or safety.get("object_body_bytes_read") != 0
    ):
        raise StageValidationError("SOURCE_SAFETY_GATE_NOT_PASS")
    with required_regular[0].open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise StageValidationError("SOURCE_BATCH_TABLE_EMPTY") from exc
        expected_header = [
            "production_batch",
            "n_studies",
            "n_subjects",
            "n_requested_objects",
            "n_verified_objects",
            "n_unexpected_selected_objects",
            "total_source_bytes",
            "status",
        ]
        normalized_header = [name.strip().casefold() for name in header]
        if header != expected_header or len(normalized_header) != len(set(normalized_header)):
            raise StageValidationError("SOURCE_BATCH_TABLE_HEADER_INVALID_OR_DUPLICATED")
        raw_rows = list(reader)
        if any(len(row) != len(header) for row in raw_rows):
            raise StageValidationError("SOURCE_BATCH_TABLE_ROW_WIDTH_INVALID")
        rows = [dict(zip(header, row)) for row in raw_rows]
    if (
        len(rows) != 19
        or {row["production_batch"] for row in rows}
        != {f"c3_batch_{index:03d}" for index in range(19)}
        or sum(int(row["n_studies"]) for row in rows) != 4530
        or sum(int(row["n_subjects"]) for row in rows) != 4530
        or sum(int(row["n_requested_objects"]) for row in rows) != 335984
        or sum(int(row["n_verified_objects"]) for row in rows) != 335984
        or sum(int(row["total_source_bytes"]) for row in rows) != summary.get("exact_source_bytes")
        or any(row["status"] != "PASS" for row in rows)
    ):
        raise StageValidationError("SOURCE_BATCH_TABLE_NOT_RECONCILED")

    selected_rows = source_preflight.load_selected_studies(
        selected_studies,
        batch_size=250,
        expected_sha256=sha256_file(selected_studies),
        expected_studies=4530,
    )
    split_by_subject = source_preflight.load_split_map(
        split_map,
        selected_rows,
        expected_sha256=sha256_file(split_map),
    )
    requests, _ = source_preflight.load_source_requests(
        source_manifest,
        selected_rows,
        split_by_subject=split_by_subject,
        expected_sha256=sha256_file(source_manifest),
    )
    expected_by_path = {row.relative_path: row for row in requests}

    detail_path = required_regular[1]
    detail_count = 0
    detail_bytes = 0
    storage_class_counts: dict[str, int] = {}
    storage_class_bytes: dict[str, int] = {}
    comparable_counts = {name: 0 for name in ("size", "md5", "crc32c", "generation")}
    mismatch_counts = {name: 0 for name in comparable_counts}
    expected_detail_keys = {
        "release_id",
        "component",
        "subject_id",
        "study_id",
        "split",
        "source_relative_path",
        "gcs_uri",
        "source_object_key",
        "production_batch",
        "remote_size_bytes",
        "remote_md5_base64",
        "remote_crc32c_base64",
        "remote_generation",
        "remote_storage_class",
        "remote_updated",
        "preflight_status",
        "discrepancy_reasons",
    }
    with detail_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                not isinstance(row, Mapping)
                or set(row) != expected_detail_keys
                or row.get("preflight_status") != "PASS"
                or row.get("discrepancy_reasons") != []
            ):
                raise StageValidationError("SOURCE_RESTRICTED_DETAIL_NOT_ALL_PASS")
            source_path = str(row.get("source_relative_path", ""))
            expected_request = expected_by_path.pop(source_path, None)
            if expected_request is None:
                raise StageValidationError("SOURCE_RESTRICTED_DETAIL_NOT_EXACT_FROZEN_SET")
            expected_identity = {
                "release_id": expected_request.release_id,
                "component": expected_request.component,
                "subject_id": expected_request.subject_id,
                "study_id": expected_request.study_id,
                "split": expected_request.split,
                "source_relative_path": expected_request.relative_path,
                "gcs_uri": expected_request.gcs_uri,
                "source_object_key": expected_request.source_object_key,
                "production_batch": expected_request.batch_id,
            }
            if any(row.get(name) != value for name, value in expected_identity.items()):
                raise StageValidationError("SOURCE_RESTRICTED_DETAIL_AUTHORITY_MISMATCH")
            remote = source_preflight.normalize_remote_metadata(
                {
                    "relative_path": source_path,
                    "size_bytes": row.get("remote_size_bytes"),
                    "md5_base64": row.get("remote_md5_base64"),
                    "crc32c_base64": row.get("remote_crc32c_base64"),
                    "generation": row.get("remote_generation"),
                    "storage_class": row.get("remote_storage_class"),
                    "updated": row.get("remote_updated"),
                }
            )
            comparisons = (
                ("size", expected_request.expected_size_bytes, remote.size_bytes),
                ("md5", expected_request.expected_md5_base64, remote.md5_base64),
                ("crc32c", expected_request.expected_crc32c_base64, remote.crc32c_base64),
                ("generation", expected_request.expected_generation, remote.generation),
            )
            for name, expected_value, observed_value in comparisons:
                if expected_value is None:
                    continue
                comparable_counts[name] += 1
                if expected_value != observed_value:
                    mismatch_counts[name] += 1
            detail_bytes += remote.size_bytes
            storage_class_counts[remote.storage_class] = storage_class_counts.get(remote.storage_class, 0) + 1
            storage_class_bytes[remote.storage_class] = storage_class_bytes.get(remote.storage_class, 0) + remote.size_bytes
            detail_count += 1
    if (
        expected_by_path
        or detail_count != 335984
        or detail_bytes != summary.get("exact_source_bytes")
        or dict(sorted(storage_class_counts.items())) != summary.get("storage_class_counts")
        or dict(sorted(storage_class_bytes.items())) != summary.get("storage_class_bytes")
        or comparable_counts != summary.get("historical_metadata_comparable_counts")
        or mismatch_counts != summary.get("historical_metadata_mismatch_counts")
        or any(mismatch_counts.values())
    ):
        raise StageValidationError("SOURCE_RESTRICTED_DETAIL_NOT_RECONCILED")
    if required_regular[2].stat().st_size != 0:
        raise StageValidationError("SOURCE_DISCREPANCY_FILE_NOT_EMPTY")


def validate_resource(
    run_root: Path,
    *,
    resource_policy: Path,
    safe_export_policy: Path,
    current_research_usage_bytes: int,
    migration_witness: Path,
) -> None:
    aggregate = run_root / "aggregate"
    plan = load_json(aggregate / "c3_full_resource_plan.json")
    summary_path = aggregate / "c3_full_source_preflight.summary.json"
    batch_path = aggregate / "c3_full_source_preflight_by_batch.csv"
    checksums = plan.get("input_checksums")
    if not isinstance(checksums, Mapping):
        raise StageValidationError("RESOURCE_INPUT_CHECKSUMS_MISSING")
    expected = {
        "source_summary_sha256": sha256_file(summary_path),
        "batch_table_sha256": sha256_file(batch_path),
        "resource_policy_sha256": sha256_file(resource_policy),
        "safe_export_policy_sha256": sha256_file(safe_export_policy),
        "migration_witness_sha256": sha256_file(migration_witness),
    }
    if checksums != expected:
        raise StageValidationError("RESOURCE_INPUT_CHECKSUMS_MISMATCH")
    summary = load_json(summary_path)
    batches = resource_planner._load_batches(batch_path)
    policy = yaml.safe_load(resource_policy.read_text(encoding="utf-8"))
    migration = resource_planner.load_migration_witness(migration_witness)
    recomputed = resource_planner.build_plan(
        summary,
        batches,
        policy,
        current_usage_override=current_research_usage_bytes,
        migration_witness=migration,
    )
    recomputed["input_checksums"] = expected
    if plan != recomputed:
        raise StageValidationError("RESOURCE_STAGE_NOT_DETERMINISTICALLY_REPRODUCIBLE")
    if (
        plan.get("status") != "PASS_RESOURCE_PLAN"
        or plan.get("headroom_gate_passed") is not True
        or plan.get("additional_tb_required") != 0
        or plan.get("raw_dicom_retention_assumed") is not True
    ):
        raise StageValidationError("RESOURCE_STAGE_NOT_GO_UNDER_QUOTA")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("storage", "source", "resource"), required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--selected-studies", type=Path)
    parser.add_argument("--split-map", type=Path)
    parser.add_argument("--resource-policy", type=Path)
    parser.add_argument("--safe-export-policy", type=Path)
    parser.add_argument("--current-research-usage-bytes", type=int)
    parser.add_argument("--migration-witness", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.stage == "storage":
            if args.safe_export_policy is None:
                raise StageValidationError("STORAGE_SAFE_EXPORT_POLICY_REQUIRED")
            validate_storage(
                args.run_root,
                safe_export_policy=args.safe_export_policy,
            )
        elif args.stage == "source":
            if args.source_manifest is None or args.selected_studies is None or args.split_map is None:
                raise StageValidationError("SOURCE_AUTHORITIES_REQUIRED")
            validate_source(
                args.run_root,
                source_manifest=args.source_manifest,
                selected_studies=args.selected_studies,
                split_map=args.split_map,
            )
        else:
            if (
                args.resource_policy is None
                or args.safe_export_policy is None
                or args.current_research_usage_bytes is None
                or args.migration_witness is None
            ):
                raise StageValidationError("RESOURCE_POLICIES_REQUIRED")
            validate_resource(
                args.run_root,
                resource_policy=args.resource_policy,
                safe_export_policy=args.safe_export_policy,
                current_research_usage_bytes=args.current_research_usage_bytes,
                migration_witness=args.migration_witness,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error_code": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": "PASS_EXISTING_STAGE_VALID", "stage": args.stage}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
