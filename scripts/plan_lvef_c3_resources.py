#!/usr/bin/env python3
"""Calculate itemized full-retention and rolling-cache C3 storage peaks."""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import yaml

from lvef_multitask_analysis_modes import (
    bind_approved_restricted_path,
    load_policy as load_safe_export_policy,
)


class ResourcePlanError(ValueError):
    pass


MIGRATION_STATES = {
    "PLANNED_NOT_EXECUTED",
    "COMPLETED_INCLUDED_IN_CURRENT_RESEARCH_USAGE",
}


def validate_scc_quota_cost_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the published SCC quota rate without inventing an invoice total."""

    quota_cost = policy.get("scc_quota_cost")
    if not isinstance(quota_cost, Mapping):
        raise ResourcePlanError("SCC_QUOTA_COST_POLICY_MISSING")
    expected_literals = {
        "status": "APPROVED_OR_IMMINENT_PENDING_PQUOTA_ACTIVATION",
        "currency": "USD",
        "rate_authority": "BOSTON_UNIVERSITY_STORAGE_AS_A_SERVICE_PUBLISHED_RATE",
        "minimum_purchase_tb": 1,
        "minimum_term_months": 6,
        "billing_basis": "fiscal-year-prorated",
        "administrative_exact_invoice": "pending-start-date-confirmation",
    }
    for key, expected in expected_literals.items():
        if quota_cost.get(key) != expected:
            raise ResourcePlanError(f"SCC_QUOTA_COST_POLICY_INVALID_{key.upper()}")
    try:
        annual_rate = Decimal(str(quota_cost["storage_as_a_service_usd_per_tb_year"]))
        six_month_estimate = Decimal(
            str(quota_cost["estimated_one_tb_six_month_cost_usd"])
        )
        twelve_month_estimate = Decimal(
            str(quota_cost["estimated_one_tb_twelve_month_cost_usd"])
        )
    except (InvalidOperation, KeyError) as exc:
        raise ResourcePlanError("SCC_QUOTA_COST_POLICY_INVALID_NUMERIC_RATE") from exc
    if annual_rate != Decimal("22.00"):
        raise ResourcePlanError("SCC_QUOTA_COST_ANNUAL_RATE_CHANGED")
    if six_month_estimate != annual_rate * Decimal(6) / Decimal(12):
        raise ResourcePlanError("SCC_QUOTA_COST_SIX_MONTH_ESTIMATE_INVALID")
    if twelve_month_estimate != annual_rate:
        raise ResourcePlanError("SCC_QUOTA_COST_TWELVE_MONTH_ESTIMATE_INVALID")
    return dict(quota_cost)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ResourcePlanError("JSON_ROOT_NOT_MAPPING")
    return payload


def load_migration_witness(path: Path) -> Mapping[str, Any]:
    """Load an SCC-only, fully classified disaster-tier migration witness."""

    witness = _load_json(path)
    required = {
        "schema_version",
        "status",
        "classification_complete",
        "migration_state",
        "disaster_tier_inventory_bytes",
        "classified_migration_bytes",
        "classified_retained_bytes",
        "inventory_sha256",
        "classification_sha256",
    }
    if not required.issubset(witness):
        raise ResourcePlanError("MIGRATION_WITNESS_SCHEMA_INCOMPLETE")
    if (
        witness.get("schema_version") != 1
        or witness.get("status") != "PASS_CLASSIFIED_MIGRATION_WITNESS"
        or witness.get("classification_complete") is not True
        or witness.get("migration_state") not in MIGRATION_STATES
    ):
        raise ResourcePlanError("MIGRATION_WITNESS_NOT_AUTHORITATIVE")
    inventory = witness.get("disaster_tier_inventory_bytes")
    migrated = witness.get("classified_migration_bytes")
    retained = witness.get("classified_retained_bytes")
    if (
        any(not isinstance(value, int) or value < 0 for value in (inventory, migrated, retained))
        or migrated + retained != inventory
    ):
        raise ResourcePlanError("MIGRATION_WITNESS_BYTES_NOT_RECONCILED")
    for key in ("inventory_sha256", "classification_sha256"):
        value = witness.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ResourcePlanError("MIGRATION_WITNESS_CHECKSUM_INVALID")
    return witness


def _load_batches(path: Path) -> list[dict[str, int | str]]:
    rows: list[dict[str, int | str]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "production_batch": str(row["production_batch"]),
                    "n_studies": int(row["n_studies"]),
                    "n_requested_objects": int(row["n_requested_objects"]),
                    "total_source_bytes": int(row["total_source_bytes"]),
                }
            )
    if not rows or len({str(row["production_batch"]) for row in rows}) != len(rows):
        raise ResourcePlanError("BATCH_TABLE_EMPTY_OR_NONUNIQUE")
    return rows


def _strategy(
    *,
    name: str,
    quota: int,
    required_headroom: int,
    components: Mapping[str, int],
) -> dict[str, Any]:
    if any(not isinstance(value, int) or value < 0 for value in components.values()):
        raise ResourcePlanError("INVALID_STORAGE_COMPONENT")
    peak = sum(components.values())
    headroom = quota - peak
    return {
        "strategy": name,
        "components_bytes": dict(components),
        "projected_peak_bytes": peak,
        "projected_peak_decimal_gb": round(peak / 10**9, 3),
        "remaining_headroom_bytes": headroom,
        "remaining_headroom_decimal_gb": round(headroom / 10**9, 3),
        "required_headroom_bytes": required_headroom,
        "go_under_quota": headroom >= required_headroom,
    }


def build_plan(
    summary: Mapping[str, Any],
    batches: Sequence[Mapping[str, int | str]],
    policy: Mapping[str, Any],
    *,
    current_usage_override: int | None = None,
    migration_witness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        summary.get("status") != "PASS_METADATA_ONLY"
        or summary.get("metadata_provider") != "GCS_JSON_API_OBJECTS_LIST"
        or summary.get("authoritative_for_full_c3") is not True
    ):
        raise ResourcePlanError("SOURCE_PREFLIGHT_NOT_PASS")
    storage = policy["storage"]
    scc_quota_cost = validate_scc_quota_cost_policy(policy)
    quota = int(storage["final_research_quota_bytes"])
    if current_usage_override is None:
        raise ResourcePlanError("CURRENT_RESEARCH_USAGE_RUNTIME_WITNESS_REQUIRED")
    current_usage = int(current_usage_override)
    if storage.get("migrated_disaster_tier_usage_bytes") != "RUNTIME_CLASSIFIED_WITNESS_REQUIRED":
        raise ResourcePlanError("POLICY_MUST_NOT_HARDCODE_MIGRATED_BYTES")
    if storage.get("provisional_backed_up_retention_is_authority") is not False:
        raise ResourcePlanError("PROVISIONAL_RETENTION_CANNOT_BE_AUTHORITY")
    if migration_witness is None:
        raise ResourcePlanError("CLASSIFIED_MIGRATION_RUNTIME_WITNESS_REQUIRED")
    migration_state = str(migration_witness.get("migration_state"))
    if (
        migration_witness.get("status") != storage["migration_witness_status_required"]
        or migration_state not in set(storage["migration_witness_states_allowed"])
        or migration_witness.get("classification_complete") is not True
        or migration_witness.get("schema_version") != 1
    ):
        raise ResourcePlanError("CLASSIFIED_MIGRATION_RUNTIME_WITNESS_INVALID")
    for key in ("inventory_sha256", "classification_sha256"):
        value = migration_witness.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ResourcePlanError("CLASSIFIED_MIGRATION_RUNTIME_WITNESS_INVALID")
    migrated_classified_bytes = int(migration_witness["classified_migration_bytes"])
    retained_classified_bytes = int(migration_witness["classified_retained_bytes"])
    if (
        migrated_classified_bytes < 0
        or retained_classified_bytes < 0
        or migrated_classified_bytes + retained_classified_bytes
        != int(migration_witness["disaster_tier_inventory_bytes"])
    ):
        raise ResourcePlanError("CLASSIFIED_MIGRATION_RUNTIME_BYTES_NOT_RECONCILED")
    migration_increment = (
        migrated_classified_bytes
        if migration_state == "PLANNED_NOT_EXECUTED"
        else 0
    )
    raw = int(summary["exact_source_bytes"])
    if sum(int(row["total_source_bytes"]) for row in batches) != raw:
        raise ResourcePlanError("BATCH_BYTES_DO_NOT_RECONCILE")
    if sum(int(row["n_requested_objects"]) for row in batches) != int(summary["requested_objects"]):
        raise ResourcePlanError("BATCH_OBJECTS_DO_NOT_RECONCILE")
    absolute = int(storage["minimum_absolute_headroom_bytes"])
    fractional = int(quota * float(storage["minimum_fractional_headroom"]))
    required_headroom = max(absolute, fractional)
    extracted_per_object = int(storage["extraction_bytes_per_source_object_upper_bound"])
    if extracted_per_object != 32 * 224 * 224 * 3:
        raise ResourcePlanError("EXTRACTION_UPPER_BOUND_CHANGED")
    full_extracted = int(summary["requested_objects"]) * extracted_per_object
    concurrency = int(storage["rolling_extracted_batch_concurrency"])
    if concurrency not in {1, 2}:
        raise ResourcePlanError("ROLLING_CONCURRENCY_MUST_BE_ONE_OR_TWO")
    extraction_by_batch = sorted(
        (int(row["n_requested_objects"]) * extracted_per_object for row in batches),
        reverse=True,
    )
    active_extracted = sum(extraction_by_batch[:concurrency])
    largest_batch = max(int(row["total_source_bytes"]) for row in batches)
    if storage.get("shared_partial_retry_buffer_is_single_nonadditive_reserve") is not True:
        raise ResourcePlanError("PARTIAL_RETRY_BUFFER_MUST_BE_NONADDITIVE")
    shared_transfer_buffer = largest_batch * int(
        storage["shared_partial_retry_buffer_batch_count"]
    )
    if int(storage["shared_partial_retry_buffer_batch_count"]) != 1:
        raise ResourcePlanError("SHARED_TRANSFER_BUFFER_MUST_EQUAL_ONE_FULL_BATCH")
    clip_embeddings = int(summary["requested_objects"]) * int(
        storage["clip_embedding_bytes_per_source_object_upper_bound"]
    )
    study_embeddings = int(summary["selected_studies"]) * int(
        storage["study_embedding_bytes_per_study_upper_bound"]
    )
    shared = {
        "current_research_usage": current_usage,
        "classified_disaster_tier_migration_increment": migration_increment,
        "selected_raw_dicoms": raw,
        "clip_embeddings_upper_bound": clip_embeddings,
        "study_embeddings_upper_bound": study_embeddings,
        "manifests_and_metadata": int(storage["manifest_and_metadata_reserve_bytes"]),
        "logs": int(storage["log_reserve_bytes"]),
        "shared_partial_retry_buffer": shared_transfer_buffer,
        "preservation_outputs": int(storage["preservation_output_reserve_bytes"]),
        "safety_reserve": int(storage["safety_reserve_bytes"]),
    }
    strategy_a = _strategy(
        name="A_FULL_RAW_PLUS_ALL_EXTRACTED",
        quota=quota,
        required_headroom=required_headroom,
        components={**shared, "active_or_retained_extracted_cache": full_extracted},
    )
    strategy_b = _strategy(
        name="B_RAW_RETAINED_ROLLING_EXTRACTED_CACHE",
        quota=quota,
        required_headroom=required_headroom,
        components={
            **shared,
            "active_extracted_cache": active_extracted,
            "retained_extracted_audit_sample": int(storage["retained_extracted_audit_sample_bytes"]),
        },
    )
    preferred = strategy_b
    another_tb_needed = not preferred["go_under_quota"]
    preferred_components = preferred["components_bytes"]
    return {
        "schema_version": 1,
        "status": "PASS_RESOURCE_PLAN" if not another_tb_needed else "FAIL_ADDITIONAL_STORAGE_REQUIRED",
        "source_preflight_status": summary["status"],
        "final_research_quota_bytes": quota,
        "quota_planning_unit": policy["units"]["quota_planning_unit"],
        "required_headroom_bytes": required_headroom,
        "minimum_absolute_headroom_bytes": absolute,
        "minimum_fractional_headroom_bytes": fractional,
        "exact_source_bytes": raw,
        "production_batches": len(batches),
        "rolling_extracted_batch_concurrency": concurrency,
        "largest_batch_source_bytes": largest_batch,
        "full_extracted_cache_upper_bound_bytes": full_extracted,
        "extraction_bytes_per_source_object_upper_bound": extracted_per_object,
        "historical_full_extracted_cache_planning_bytes": int(
            storage["historical_full_extracted_cache_planning_bytes"]
        ),
        "rolling_active_extracted_cache_upper_bound_bytes": active_extracted,
        "strategies": [strategy_a, strategy_b],
        "preferred_strategy": strategy_b["strategy"],
        "preferred_projected_peak_bytes": strategy_b["projected_peak_bytes"],
        "preferred_remaining_headroom_bytes": strategy_b["remaining_headroom_bytes"],
        "preferred_go_under_2tb_quota": strategy_b["go_under_quota"],
        "another_1tb_increment_needed": another_tb_needed,
        "raw_dicom_retention_assumed": True,
        "extracted_cache_retirement_requires_all_batch_gates": True,
        "strategy": preferred["strategy"],
        "quota_bytes": quota,
        "current_usage_bytes": current_usage + migration_increment,
        "existing_research_usage_bytes": current_usage,
        "migrated_disaster_tier_usage_bytes": migrated_classified_bytes,
        "migration_increment_in_peak_bytes": migration_increment,
        "migration_witness_status": migration_witness["status"],
        "migration_state": migration_state,
        "disaster_tier_inventory_bytes": int(
            migration_witness["disaster_tier_inventory_bytes"]
        ),
        "classified_retained_disaster_tier_bytes": retained_classified_bytes,
        "full_200gb_reallocation_supported_by_path_classification": (
            retained_classified_bytes == 0
        ),
        "full_200gb_reallocation_completed_or_administratively_approved": False,
        "provisional_50gb_retention_is_authority": bool(
            storage["provisional_backed_up_retention_is_authority"]
        ),
        "raw_source_bytes": raw,
        "active_extraction_cache_bytes": preferred_components["active_extracted_cache"],
        "clip_embedding_bytes": preferred_components["clip_embeddings_upper_bound"],
        "study_embedding_bytes": preferred_components["study_embeddings_upper_bound"],
        "manifest_bytes": preferred_components["manifests_and_metadata"],
        "log_bytes": preferred_components["logs"],
        "shared_partial_retry_buffer_bytes": preferred_components[
            "shared_partial_retry_buffer"
        ],
        "preservation_bytes": preferred_components["preservation_outputs"],
        "safety_reserve_bytes": preferred_components["safety_reserve"],
        "projected_peak_bytes": preferred["projected_peak_bytes"],
        "projected_headroom_bytes": preferred["remaining_headroom_bytes"],
        "headroom_gate_passed": preferred["go_under_quota"],
        "additional_tb_required": 1 if another_tb_needed else 0,
        "scc_quota_cost_authority": scc_quota_cost,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--batch-table", type=Path, required=True)
    parser.add_argument("--resource-policy", type=Path, required=True)
    parser.add_argument(
        "--safe-export-policy",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "configs"
        / "lvef_multitask_safe_export_policy.yaml",
    )
    parser.add_argument("--current-research-usage-bytes", type=int)
    parser.add_argument("--migration-witness", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        safe_policy, _ = load_safe_export_policy(args.safe_export_policy)
        source_summary = bind_approved_restricted_path(
            args.source_summary,
            policy=safe_policy,
            must_exist=True,
            expect="file",
        )
        batch_table = bind_approved_restricted_path(
            args.batch_table,
            policy=safe_policy,
            must_exist=True,
            expect="file",
        )
        output = bind_approved_restricted_path(
            args.output,
            policy=safe_policy,
            must_exist=False,
            expect="file",
            root_kind="staging",
            create=True,
        )
        migration_witness_path = bind_approved_restricted_path(
            args.migration_witness,
            policy=safe_policy,
            must_exist=True,
            expect="file",
        )
        summary = _load_json(source_summary)
        policy = yaml.safe_load(args.resource_policy.read_text(encoding="utf-8"))
        batches = _load_batches(batch_table)
        migration_witness = load_migration_witness(migration_witness_path)
        plan = build_plan(
            summary,
            batches,
            policy,
            current_usage_override=args.current_research_usage_bytes,
            migration_witness=migration_witness,
        )
        plan["input_checksums"] = {
            "source_summary_sha256": _sha256_file(source_summary),
            "batch_table_sha256": _sha256_file(batch_table),
            "resource_policy_sha256": _sha256_file(args.resource_policy),
            "safe_export_policy_sha256": _sha256_file(args.safe_export_policy),
            "migration_witness_sha256": _sha256_file(migration_witness_path),
        }
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(json.dumps({"status": "FAIL", "error_code": str(exc)}, sort_keys=True))
        return 2
    if output.exists():
        print(json.dumps({"status": "FAIL", "error_code": "RESOURCE_OUTPUT_ALREADY_EXISTS"}))
        return 2
    output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: plan[key] for key in (
        "status",
        "preferred_projected_peak_bytes",
        "preferred_remaining_headroom_bytes",
        "another_1tb_increment_needed",
    )}, sort_keys=True))
    return 0 if plan["status"] == "PASS_RESOURCE_PLAN" else 4


if __name__ == "__main__":
    sys.exit(main())
