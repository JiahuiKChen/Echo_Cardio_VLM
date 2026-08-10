#!/usr/bin/env python3
"""Validate one restricted, stage-specific future C3 scheduler authorization."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_lvef_c3_production_launch_authority as launch


STAGE_RANGES = {
    "FIRST_BATCH_DOWNLOAD": "1",
    "REMAINING_BATCH_DOWNLOAD": "2-19",
    "DICOM_EXTRACTION": "1-19",
    "ECHOPRIME_EMBEDDING": "1-19",
    "BATCH_PRESERVATION": "1-19",
    "CACHE_RETIREMENT": "1-19",
    "PRESERVATION_FINALIZATION": "none",
}
SINGLE_BATCH_STAGES = {
    "REMAINING_BATCH_DOWNLOAD",
    "DICOM_EXTRACTION",
    "ECHOPRIME_EMBEDDING",
    "BATCH_PRESERVATION",
    "CACHE_RETIREMENT",
}
KEYS = {
    "schema_version",
    "artifact_type",
    "status",
    "stage",
    "authorized_array_range",
    "governing_commit",
    "attempt_id",
    "orchestration_contract_sha256",
    "batch_plan_sha256",
    "execution_environment_sha256",
    "launch_authority_sha256",
    "post_expansion_capacity_summary_sha256",
    "production_authority_packet_sha256",
    "owner_authorized",
    "owner_authorization_date",
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class DispatchAuthorizationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DispatchAuthorizationError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise DispatchAuthorizationError("AUTHORITY_FILE_NOT_REGULAR")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(
    receipt_path: Path,
    *,
    stage: str,
    governing_commit: str,
    orchestration_contract: Path,
    batch_plan: Path,
    execution_environment: Path,
    launch_authority: Path,
    attempt_id: str,
    authorized_array_range: str | None = None,
) -> None:
    if (
        stage not in STAGE_RANGES
        or not COMMIT_RE.fullmatch(governing_commit)
        or not launch.ATTEMPT_RE.fullmatch(attempt_id)
    ):
        raise DispatchAuthorizationError("DISPATCH_ARGUMENT_INVALID")
    try:
        launch_value = launch._load_json(launch_authority, "LAUNCH_AUTHORITY")
        launch.validate(
            launch_value,
            envelope_path=launch_authority,
            attempt_id=attempt_id,
            governing_commit=governing_commit,
            execution_environment=execution_environment,
        )
    except launch.LaunchAuthorityError as exc:
        raise DispatchAuthorizationError("LAUNCH_AUTHORITY_NOT_PASS") from exc
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise DispatchAuthorizationError("DISPATCH_RECEIPT_NOT_REGULAR")
    try:
        receipt = json.loads(
            receipt_path.read_text(encoding="utf-8"), object_pairs_hook=_pairs
        )
    except DispatchAuthorizationError:
        raise
    except Exception as exc:
        raise DispatchAuthorizationError("DISPATCH_RECEIPT_INVALID_JSON") from exc
    if not isinstance(receipt, dict) or set(receipt) != KEYS:
        raise DispatchAuthorizationError("DISPATCH_RECEIPT_SCHEMA_MISMATCH")
    requested_range = authorized_array_range or STAGE_RANGES[stage]
    if stage in SINGLE_BATCH_STAGES:
        try:
            task_number = int(requested_range)
        except ValueError as exc:
            raise DispatchAuthorizationError("DISPATCH_ARRAY_SCOPE_INVALID") from exc
        minimum_task = 2 if stage == "REMAINING_BATCH_DOWNLOAD" else 1
        if task_number < minimum_task or task_number > 19 or str(task_number) != requested_range:
            raise DispatchAuthorizationError("DISPATCH_ARRAY_SCOPE_INVALID")
    elif requested_range != STAGE_RANGES[stage]:
        raise DispatchAuthorizationError("DISPATCH_ARRAY_SCOPE_INVALID")
    expected = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_restricted_dispatch_authorization_v1",
        "status": "AUTHORIZED",
        "stage": stage,
        "authorized_array_range": requested_range,
        "governing_commit": governing_commit,
        "attempt_id": attempt_id,
        "orchestration_contract_sha256": sha256_file(orchestration_contract),
        "batch_plan_sha256": sha256_file(batch_plan),
        "execution_environment_sha256": sha256_file(execution_environment),
        "launch_authority_sha256": sha256_file(launch_authority),
        "post_expansion_capacity_summary_sha256": launch_value[
            "post_expansion_capacity_summary"
        ]["sha256"],
        "production_authority_packet_sha256": launch_value[
            "production_authority_packet"
        ]["sha256"],
        "owner_authorized": True,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise DispatchAuthorizationError("DISPATCH_RECEIPT_AUTHORITY_MISMATCH")
    if not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", str(receipt.get("owner_authorization_date"))):
        raise DispatchAuthorizationError("DISPATCH_RECEIPT_DATE_INVALID")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--stage", choices=sorted(STAGE_RANGES), required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--orchestration-contract", type=Path, required=True)
    parser.add_argument("--batch-plan", type=Path, required=True)
    parser.add_argument("--execution-environment", type=Path, required=True)
    parser.add_argument("--launch-authority", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--authorized-array-range")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate(
            args.receipt,
            stage=args.stage,
            governing_commit=args.governing_commit,
            orchestration_contract=args.orchestration_contract,
            batch_plan=args.batch_plan,
            execution_environment=args.execution_environment,
            launch_authority=args.launch_authority,
            attempt_id=args.attempt_id,
            authorized_array_range=args.authorized_array_range,
        )
    except DispatchAuthorizationError as exc:
        print(f"C3_DISPATCH_AUTHORIZATION=BLOCKED_{exc.code}")
        return 78
    print("C3_DISPATCH_AUTHORIZATION=PASS")
    print(f"C3_DISPATCH_STAGE={args.stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
