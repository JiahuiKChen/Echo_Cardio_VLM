#!/usr/bin/env python3
"""Build one no-clobber, owner-affirmed C3 authorization receipt offline.

This builder creates only a requested receipt.  It cannot submit a scheduler
job, contact a cloud service, read a DICOM body, run an imaging stage, retire a
cache, or grant a second scope implicitly.  Every receipt is bound to the final
launch-authority envelope and written to its exact pre-created private root.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_lvef_c3_production_authority_packet as packet
import build_lvef_c3_production_launch_authority as launch
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages
import retire_lvef_c3_extracted_cache_v2 as retirement
import validate_lvef_c3_dispatch_authorization as dispatch


class OwnerAuthorizationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


STAGE_ROOT_KEYS = {
    "DICOM_EXTRACTION": "LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT",
    "ECHOPRIME_EMBEDDING": "LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT",
    "BATCH_PRESERVATION": "LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT",
    "PRESERVATION_FINALIZATION": "LVEF_C3_FINALIZATION_AUTHORIZATION_ROOT",
}


def _runtime(args: argparse.Namespace) -> tuple[Mapping[str, str], Mapping[str, Any], str]:
    if args.owner_authorization_affirmed != "YES":
        raise OwnerAuthorizationError("OWNER_AUTHORIZATION_NOT_AFFIRMED")
    values = packet._parse_execution_environment(args.execution_environment)
    launch_value = launch._load_json(args.launch_authority, "LAUNCH_AUTHORITY")
    launch.validate(
        launch_value,
        envelope_path=args.launch_authority,
        attempt_id=values["LVEF_C3_ATTEMPT_ID"],
        governing_commit=values["LVEF_C3_GOVERNING_COMMIT"],
        execution_environment=args.execution_environment,
    )
    return values, launch_value, launch.sha256_file(args.launch_authority)


def _date(value: str) -> str:
    if not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", value):
        raise OwnerAuthorizationError("OWNER_AUTHORIZATION_DATE_INVALID")
    return value


def _utc(value: str, code: str) -> str:
    if not value.endswith("Z"):
        raise OwnerAuthorizationError(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise OwnerAuthorizationError(code) from exc
    if parsed.tzinfo != timezone.utc:
        raise OwnerAuthorizationError(code)
    return value


def _expected_output(root: str, filename: str, output: Path) -> None:
    expected = Path(root) / filename
    if output != expected:
        raise OwnerAuthorizationError("AUTHORIZATION_OUTPUT_PATH_MISMATCH")


def _write(output: Path, value: Mapping[str, Any]) -> None:
    try:
        launch.write_no_clobber(output, value)
    except launch.LaunchAuthorityError as exc:
        raise OwnerAuthorizationError(str(exc)) from exc


def build_dispatch(args: argparse.Namespace) -> Mapping[str, Any]:
    values, launch_value, launch_sha = _runtime(args)
    if args.stage not in dispatch.STAGE_RANGES:
        raise OwnerAuthorizationError("DISPATCH_STAGE_INVALID")
    _expected_output(
        values["LVEF_C3_DISPATCH_AUTHORIZATION_ROOT"],
        f"{args.stage}.{args.task_scope}.dispatch_authorization.json",
        args.output,
    )
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_restricted_dispatch_authorization_v1",
        "status": "AUTHORIZED",
        "stage": args.stage,
        "authorized_array_range": args.task_scope,
        "governing_commit": values["LVEF_C3_GOVERNING_COMMIT"],
        "attempt_id": values["LVEF_C3_ATTEMPT_ID"],
        "orchestration_contract_sha256": core.sha256_file(
            Path(values["LVEF_C3_ORCHESTRATION_CONTRACT"])
        ),
        "batch_plan_sha256": core.sha256_file(Path(values["LVEF_C3_BATCH_PLAN"])),
        "execution_environment_sha256": core.sha256_file(args.execution_environment),
        "launch_authority_sha256": launch_sha,
        "post_expansion_capacity_summary_sha256": launch_value[
            "post_expansion_capacity_summary"
        ]["sha256"],
        "production_authority_packet_sha256": launch_value[
            "production_authority_packet"
        ]["sha256"],
        "owner_authorized": True,
        "owner_authorization_date": _date(args.owner_authorization_date),
    }
    _write(args.output, value)
    dispatch.validate(
        args.output,
        stage=args.stage,
        governing_commit=values["LVEF_C3_GOVERNING_COMMIT"],
        orchestration_contract=Path(values["LVEF_C3_ORCHESTRATION_CONTRACT"]),
        batch_plan=Path(values["LVEF_C3_BATCH_PLAN"]),
        execution_environment=args.execution_environment,
        launch_authority=args.launch_authority,
        attempt_id=values["LVEF_C3_ATTEMPT_ID"],
        authorized_array_range=args.task_scope,
    )
    return value


def build_body(args: argparse.Namespace) -> Mapping[str, Any]:
    values, _, launch_sha = _runtime(args)
    if not core.BATCH_RE.fullmatch(args.batch_id):
        raise OwnerAuthorizationError("BODY_BATCH_ID_INVALID")
    _expected_output(
        values["LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT"],
        f"{args.batch_id}.authorization.json",
        args.output,
    )
    contract_path = Path(values["LVEF_C3_ORCHESTRATION_CONTRACT"])
    contract = core.load_orchestration_contract(contract_path)
    plan = core.load_strict_json(Path(values["LVEF_C3_BATCH_PLAN"]))
    ledger = core.load_strict_json(
        Path(values["LVEF_C3_BATCH_LEDGER_ROOT"]) / f"{args.batch_id}.initial.json"
    )
    planned = next((row for row in plan["batches"] if row["batch_id"] == args.batch_id), None)
    if planned is None:
        raise OwnerAuthorizationError("BODY_BATCH_NOT_PLANNED")
    scope = "FIRST_BATCH_ONLY" if args.batch_id == "c3_batch_000" else "REMAINING_BATCHES"
    value = {
        "schema_version": 2,
        "receipt_type": "lvef_c3_body_transfer_authorization_v2",
        "status": "AUTHORIZED_C3_DICOM_BODY_TRANSFER",
        "attempt_id": values["LVEF_C3_ATTEMPT_ID"],
        "scope": scope,
        "batch_ids": [args.batch_id],
        "authority_sha256": core.canonical_json_sha256(ledger["authority"]),
        "batch_plan_sha256": ledger["authority"]["batch_plan_sha256"],
        "launch_authority_sha256": launch_sha,
        "maximum_requests": int(planned["n_objects"])
        * int(contract["downloader"]["maximum_attempts_per_object"]),
        "issued_at_utc": _utc(args.issued_at_utc, "BODY_ISSUED_AT_INVALID"),
        "expires_at_utc": _utc(args.expires_at_utc, "BODY_EXPIRES_AT_INVALID"),
        "owner_authorization_recorded": True,
        "body_download_only": True,
        "scientific_actions_authorized": False,
    }
    core.validate_body_transfer_authorization(
        value,
        ledger=ledger,
        plan=plan,
        batch_id=args.batch_id,
        maximum_attempts_per_object=int(contract["downloader"]["maximum_attempts_per_object"]),
        expected_launch_authority_sha256=launch_sha,
    )
    _write(args.output, value)
    return value


def build_stage(args: argparse.Namespace) -> Mapping[str, Any]:
    values, _, launch_sha = _runtime(args)
    if args.stage not in STAGE_ROOT_KEYS:
        raise OwnerAuthorizationError("SCIENTIFIC_STAGE_INVALID")
    batch_id = "all_batches" if args.stage == "PRESERVATION_FINALIZATION" else args.batch_id
    if batch_id is None:
        raise OwnerAuthorizationError("SCIENTIFIC_BATCH_ID_MISSING")
    filename = "all_batches.authorization.json" if batch_id == "all_batches" else f"{batch_id}.authorization.json"
    _expected_output(values[STAGE_ROOT_KEYS[args.stage]], filename, args.output)
    value = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_restricted_stage_authorization_v1",
        "status": "AUTHORIZED",
        "authorization_scope": stages.STAGE_AUTHORIZATION_SCOPES[args.stage],
        "stage": args.stage,
        "batch_id": batch_id,
        "attempt_id": values["LVEF_C3_ATTEMPT_ID"],
        "governing_commit": values["LVEF_C3_GOVERNING_COMMIT"],
        "orchestration_contract_sha256": core.sha256_file(
            Path(values["LVEF_C3_ORCHESTRATION_CONTRACT"])
        ),
        "batch_plan_sha256": core.sha256_file(Path(values["LVEF_C3_BATCH_PLAN"])),
        "launch_authority_sha256": launch_sha,
        "owner_authorized": True,
        "owner_authorization_date": _date(args.owner_authorization_date),
    }
    stages.validate_stage_authorization_value(
        value,
        stage=args.stage,
        batch_id=batch_id,
        attempt_id=values["LVEF_C3_ATTEMPT_ID"],
        governing_commit=values["LVEF_C3_GOVERNING_COMMIT"],
        orchestration_contract_sha256=value["orchestration_contract_sha256"],
        batch_plan_sha256=value["batch_plan_sha256"],
        launch_authority_sha256=launch_sha,
    )
    _write(args.output, value)
    return value


def build_cache_retirement(args: argparse.Namespace) -> Mapping[str, Any]:
    values, _, launch_sha = _runtime(args)
    if not core.BATCH_RE.fullmatch(args.batch_id):
        raise OwnerAuthorizationError("CACHE_BATCH_ID_INVALID")
    _expected_output(
        values["LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT"],
        f"{args.batch_id}.authorization.json",
        args.output,
    )
    context = retirement.validate_gate(
        contract_path=Path(values["LVEF_C3_ORCHESTRATION_CONTRACT"]),
        plan_path=Path(values["LVEF_C3_BATCH_PLAN"]),
        environment_receipt=Path(values["LVEF_C3_ENVIRONMENT_RECEIPT"]),
        production_root=Path(values["LVEF_C3_PRODUCTION_ROOT"]),
        attempt_id=values["LVEF_C3_ATTEMPT_ID"],
        batch_id=args.batch_id,
        governing_commit=values["LVEF_C3_GOVERNING_COMMIT"],
        final_ledger_path=args.final_ledger,
        preservation_receipt_path=args.preservation_receipt,
        authorization_receipt_path=args.output,
        launch_authority_sha256=launch_sha,
        require_authorization=False,
    )
    value = {
        "schema_version": 2,
        "artifact_type": "lvef_c3_cache_retirement_owner_authorization_v2",
        "status": "AUTHORIZED_EXTRACTED_CACHE_RETIREMENT",
        "authorization_scope": "EXTRACTED_CACHE_RETIREMENT",
        "owner_authorized": True,
        "owner_authorization_date_utc": _utc(
            args.owner_authorization_date_utc,
            "CACHE_AUTHORIZATION_DATE_INVALID",
        ),
        "attempt_id": values["LVEF_C3_ATTEMPT_ID"],
        "batch_id": args.batch_id,
        "authority_sha256": core.canonical_json_sha256(context["ledger"]["authority"]),
        "preservation_receipt_sha256": core.sha256_file(args.preservation_receipt),
        "cache_inventory_sha256": context["cache_tree_sha256"],
        "launch_authority_sha256": launch_sha,
    }
    contract = core.load_orchestration_contract(
        Path(values["LVEF_C3_ORCHESTRATION_CONTRACT"])
    )
    decision = core.evaluate_cache_retirement(
        context["ledger"],
        batch_id=args.batch_id,
        target_kind="extracted_cache",
        contract=contract,
        owner_authorization=value,
        expected_launch_authority_sha256=launch_sha,
    )
    if decision.get("authorized") is not True:
        raise OwnerAuthorizationError("CACHE_RETIREMENT_POLICY_GATE_NOT_PASS")
    _write(args.output, value)
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--execution-environment", type=Path, required=True)
    common.add_argument("--launch-authority", type=Path, required=True)
    common.add_argument("--owner-authorization-affirmed", choices=["YES"], required=True)
    common.add_argument("--owner-authorization-date", required=True)
    common.add_argument("--output", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    dispatch_parser = subparsers.add_parser("dispatch", parents=[common])
    dispatch_parser.add_argument("--stage", choices=sorted(dispatch.STAGE_RANGES), required=True)
    dispatch_parser.add_argument("--task-scope", required=True)
    body = subparsers.add_parser("body-transfer", parents=[common])
    body.add_argument("--batch-id", required=True)
    body.add_argument("--issued-at-utc", required=True)
    body.add_argument("--expires-at-utc", required=True)
    stage = subparsers.add_parser("scientific-stage", parents=[common])
    stage.add_argument("--stage", choices=sorted(STAGE_ROOT_KEYS), required=True)
    stage.add_argument("--batch-id")
    cache = subparsers.add_parser("cache-retirement", parents=[common])
    cache.add_argument("--batch-id", required=True)
    cache.add_argument("--owner-authorization-date-utc", required=True)
    cache.add_argument("--final-ledger", type=Path, required=True)
    cache.add_argument("--preservation-receipt", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "dispatch":
            build_dispatch(args)
        elif args.command == "body-transfer":
            build_body(args)
        elif args.command == "scientific-stage":
            build_stage(args)
        elif args.command == "cache-retirement":
            build_cache_retirement(args)
        else:
            raise OwnerAuthorizationError("AUTHORIZATION_COMMAND_INVALID")
    except (OwnerAuthorizationError, launch.LaunchAuthorityError, core.OrchestrationError, stages.ProductionStageError, dispatch.DispatchAuthorizationError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            code = "OWNER_AUTHORIZATION_BUILD_FAILED"
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 78
    except Exception:
        print(json.dumps({"status": "FAIL", "error_code": "OWNER_AUTHORIZATION_UNEXPECTED_SANITIZED"}, sort_keys=True))
        return 78
    print(
        json.dumps(
            {
                "status": "PASS_SINGLE_OWNER_AUTHORIZATION_RECEIPT_CREATED",
                "scope_count": 1,
                "scheduler_submission_performed": False,
                "cloud_requests_performed": 0,
                "object_bodies_downloaded": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
