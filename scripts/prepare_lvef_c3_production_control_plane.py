#!/usr/bin/env python3
"""Prepare the offline C3 plan, ledgers, and private runtime authority.

This command has no network, scheduler, DICOM, extraction, embedding, deletion,
or modeling operation. The requester-pays project is accepted only through the
private process environment and is never printed or placed in argv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages


ATTEMPT_RE = re.compile(r"^lvef_c3_phase1ee_[a-z0-9][a-z0-9_-]{5,63}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PRIVATE_BILLING_ENV = "LVEF_C3_GCP_BILLING_PROJECT"


class ControlPlanePreparationError(RuntimeError):
    pass


def _git(checkout: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ControlPlanePreparationError("GIT_AUTHORITY_INSPECTION_FAILED")
    return completed.stdout.strip()


def _require_owner_private(path: Path, code: str) -> None:
    _require_no_symlink_ancestors(path, code)
    if path.is_symlink() or not path.is_file():
        raise ControlPlanePreparationError(f"{code}_NOT_REGULAR")
    metadata = path.stat(follow_symlinks=False)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ControlPlanePreparationError(f"{code}_NOT_OWNER_PRIVATE")


def _require_projectnb_directory(path: Path, code: str, *, create: bool = False) -> None:
    if not path.is_absolute() or not str(path).startswith("/restricted/projectnb/"):
        raise ControlPlanePreparationError(f"{code}_OUTSIDE_PROJECTNB")
    cursor = Path("/")
    for part in path.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise ControlPlanePreparationError(f"{code}_SYMLINK_ANCESTOR")
    if not path.exists() and create:
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise ControlPlanePreparationError(f"{code}_PARENT_INVALID")
        path.mkdir(mode=0o700)
    if (
        path.is_symlink()
        or not path.is_dir()
        or path.stat().st_uid != os.getuid()
        or stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o700
    ):
        raise ControlPlanePreparationError(f"{code}_INVALID")


def _require_no_symlink_ancestors(path: Path, code: str) -> None:
    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
        cursor /= part
        try:
            metadata = os.lstat(cursor)
        except OSError as exc:
            raise ControlPlanePreparationError(f"{code}_ANCESTOR_MISSING") from exc
        if stat.S_ISLNK(metadata.st_mode):
            if sys.platform == "darwin" and cursor == Path("/var"):
                continue
            raise ControlPlanePreparationError(f"{code}_SYMLINK_ANCESTOR")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ControlPlanePreparationError(f"{code}_ANCESTOR_NOT_DIRECTORY")


def _write_text_no_clobber(path: Path, payload: str, *, mode: int = 0o600) -> str:
    if path.exists() or path.is_symlink():
        raise ControlPlanePreparationError("CONTROL_PLANE_OUTPUT_COLLISION")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_json_no_clobber(path: Path, value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    return _write_text_no_clobber(path, payload)


def _runtime_environment_text(values: Mapping[str, str]) -> str:
    if any(
        not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
        or not re.fullmatch(r"[A-Za-z0-9_@%+,./:=-]+", value)
        for key, value in values.items()
    ):
        raise ControlPlanePreparationError("RUNTIME_ENVIRONMENT_VALUE_NOT_LITERAL")
    return "".join(f"{key}={values[key]}\n" for key in sorted(values))


def prepare(args: argparse.Namespace) -> Mapping[str, Any]:
    if not COMMIT_RE.fullmatch(args.governing_commit) or not ATTEMPT_RE.fullmatch(
        args.attempt_id
    ):
        raise ControlPlanePreparationError("CONTROL_PLANE_IDENTITY_INVALID")
    _require_no_symlink_ancestors(args.checkout_root / ".authority_leaf", "CHECKOUT")
    checkout = args.checkout_root.resolve(strict=True)
    if (
        args.checkout_root.is_symlink()
        or _git(checkout, "rev-parse", "--show-toplevel") != str(checkout)
        or _git(checkout, "branch", "--show-current")
        != "codex/lvef-multitask-revalidation"
        or _git(checkout, "rev-parse", "HEAD") != args.governing_commit
        or _git(checkout, "status", "--porcelain", "--untracked-files=no")
    ):
        raise ControlPlanePreparationError("CHECKOUT_AUTHORITY_MISMATCH")
    production_root = args.production_root
    _require_projectnb_directory(production_root, "PRODUCTION_ROOT", create=True)
    attempts_root = production_root / "attempts"
    _require_projectnb_directory(attempts_root, "ATTEMPTS_ROOT", create=True)
    attempt_root = attempts_root / args.attempt_id
    if attempt_root.exists() or attempt_root.is_symlink():
        raise ControlPlanePreparationError("ATTEMPT_ALREADY_EXISTS_NO_CLOBBER")

    contract = core.load_orchestration_contract(args.contract)
    if str(production_root) != str(contract["storage"]["production_root"]):
        raise ControlPlanePreparationError("PRODUCTION_ROOT_CONTRACT_MISMATCH")
    requirements = core.production_requirements(contract)
    for input_path, code in (
        (args.contract, "CONTRACT"),
        (args.selected, "SELECTED"),
        (args.source, "SOURCE"),
        (args.source_metadata, "SOURCE_METADATA"),
        (args.split, "SPLIT"),
        (args.checkpoint, "CHECKPOINT"),
        (args.environment_receipt, "ENVIRONMENT_RECEIPT"),
        (args.state_machine_schema, "STATE_MACHINE_SCHEMA"),
        (args.resume_ledger_schema, "RESUME_LEDGER_SCHEMA"),
        (args.gcloud_executable, "GCLOUD_EXECUTABLE"),
        (args.gcloud_resolution_receipt, "GCLOUD_RESOLUTION_RECEIPT"),
        (args.cloudsdk_config_receipt, "CLOUDSDK_CONFIG_RECEIPT"),
    ):
        _require_no_symlink_ancestors(input_path, code)
    for path, expected, code in (
        (args.selected, core.EXPECTED_SELECTED_MANIFEST_SHA256, "SELECTED"),
        (args.source, core.EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256, "SOURCE"),
        (args.split, core.EXPECTED_SPLIT_MAP_SHA256, "SPLIT"),
        (args.checkpoint, core.EXPECTED_CHECKPOINT_SHA256, "CHECKPOINT"),
    ):
        if core.sha256_file(path) != expected:
            raise ControlPlanePreparationError(f"{code}_AUTHORITY_HASH_MISMATCH")
    if core.sha256_file(Path(sys.executable).resolve()) != args.python_sha256:
        raise ControlPlanePreparationError("RUNNING_PYTHON_AUTHORITY_MISMATCH")
    stages.validate_checkpoint_and_environment(args.checkpoint, args.environment_receipt)
    environment_authority = core.load_strict_json(args.environment_receipt)
    if (
        not isinstance(environment_authority, Mapping)
        or environment_authority.get("governing_commit") != args.governing_commit
    ):
        raise ControlPlanePreparationError("ENVIRONMENT_GOVERNING_COMMIT_MISMATCH")

    _require_owner_private(args.gcloud_resolution_receipt, "GCLOUD_RESOLUTION_RECEIPT")
    if (
        args.cloudsdk_config_receipt.resolve(strict=True)
        != args.gcloud_resolution_receipt.resolve(strict=True)
    ):
        raise ControlPlanePreparationError("CLOUDSDK_RECEIPT_NOT_RESOLUTION_RECEIPT")
    provider = core.GcloudADCTokenProvider(
        args.gcloud_executable,
        cloudsdk_config=args.cloudsdk_config,
        authority_receipt=args.gcloud_resolution_receipt,
        authority_receipt_sha256=core.sha256_file(args.gcloud_resolution_receipt),
    )
    gcloud_authority = provider.validate_authority()

    billing_project = os.environ.get(PRIVATE_BILLING_ENV, "")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,62}[a-z0-9]", billing_project):
        raise ControlPlanePreparationError("PRIVATE_BILLING_PROJECT_MISSING_OR_INVALID")
    if args.extraction_workers < 1 or args.embedding_batch_size < 1:
        raise ControlPlanePreparationError("PRODUCTION_WORKER_OR_BATCH_SIZE_INVALID")

    attempt_root.mkdir(mode=0o700)
    _require_projectnb_directory(attempt_root, "ATTEMPT_ROOT")
    roots = {
        name: attempt_root / name
        for name in (
            "aggregate",
            "authority",
            "initial_ledgers",
            "authorizations",
            "batches",
            "raw",
            "extracted_cache",
            "retired_cache_staging",
            "jobs",
            "scheduler_logs",
            "scheduler_work",
            "state",
        )
    }
    for path in roots.values():
        path.mkdir(mode=0o700)
        _require_projectnb_directory(path, "ATTEMPT_CHILD")
    authorization_roots = {
        name: roots["authorizations"] / name
        for name in (
            "dispatch",
            "download",
            "extraction",
            "echoprime",
            "preservation",
            "cache_retirement",
            "finalization",
        )
    }
    for path in authorization_roots.values():
        path.mkdir(mode=0o700)
        _require_projectnb_directory(path, "AUTHORIZATION_ROOT")

    plan_authority = {
        "git_commit": args.governing_commit,
        "orchestration_contract_sha256": core.sha256_file(args.contract),
        "selected_manifest_sha256": core.sha256_file(args.selected),
        "selected_source_manifest_sha256": core.sha256_file(args.source),
        "source_metadata_sha256": core.sha256_file(args.source_metadata),
        "split_map_sha256": core.sha256_file(args.split),
        "checkpoint_sha256": core.sha256_file(args.checkpoint),
        "environment_receipt_sha256": core.sha256_file(args.environment_receipt),
        "state_machine_schema_sha256": core.sha256_file(args.state_machine_schema),
        "resume_ledger_schema_sha256": core.sha256_file(args.resume_ledger_schema),
        **gcloud_authority,
    }
    core.validate_plan_authority_against_contract(
        plan_authority, contract=contract, contract_path=args.contract
    )
    plan_authority_path = roots["authority"] / "plan_authority.restricted.json"
    _write_json_no_clobber(plan_authority_path, plan_authority)

    source_rows = core.reconcile_selected_source_metadata(
        core._read_csv_rows(args.source),
        core._read_jsonl_rows(args.source_metadata),
        release=str(contract["cohort"]["release"]),
    )
    plan = core.build_immutable_batch_plan(
        core._read_csv_rows(args.selected),
        source_rows,
        core._read_csv_rows(args.split),
        requirements=requirements,
        authority=plan_authority,
    )
    plan_path = roots["authority"] / "batch_plan.restricted.json"
    plan_sha = core.atomic_write_json_no_clobber(
        plan_path, plan, attempt_id=args.attempt_id
    )
    aggregate_plan = core.aggregate_batch_plan(plan, requirements=requirements)
    aggregate_plan_path = roots["aggregate"] / "lvef_c3_batch_plan.summary.json"
    aggregate_plan_sha = core.atomic_write_json_no_clobber(
        aggregate_plan_path, aggregate_plan, attempt_id=args.attempt_id
    )
    runtime_authority = {**plan_authority, "batch_plan_sha256": plan_sha}
    runtime_authority_path = roots["authority"] / "runtime_authority.restricted.json"
    _write_json_no_clobber(runtime_authority_path, runtime_authority)
    ledger_hashes: list[str] = []
    for batch in plan["batches"]:
        batch_id = str(batch["batch_id"])
        ledger = core.initialize_resume_ledger(
            plan,
            requirements=requirements,
            attempt_id=args.attempt_id,
            authority=runtime_authority,
            batch_ids=[batch_id],
        )
        ledger_hashes.append(
            core.atomic_write_json_no_clobber(
                roots["initial_ledgers"] / f"{batch_id}.initial.json",
                ledger,
                attempt_id=args.attempt_id,
            )
        )

    environment_values = {
        "LVEF_C3_GOVERNING_COMMIT": args.governing_commit,
        "LVEF_C3_ATTEMPT_ID": args.attempt_id,
        "LVEF_C3_ORCHESTRATION_CONTRACT": str(args.contract.resolve(strict=True)),
        "LVEF_C3_ORCHESTRATION_CONTRACT_SHA256": core.sha256_file(args.contract),
        "LVEF_C3_BATCH_PLAN": str(plan_path),
        "LVEF_C3_BATCH_PLAN_SHA256": plan_sha,
        "LVEF_C3_PRODUCTION_ROOT": str(production_root),
        "LVEF_C3_PYTHON": str(Path(sys.executable).resolve(strict=True)),
        "LVEF_C3_PYTHON_SHA256": args.python_sha256,
        "LVEF_C3_ENVIRONMENT_RECEIPT": str(args.environment_receipt.resolve(strict=True)),
        "LVEF_C3_ENVIRONMENT_RECEIPT_SHA256": core.sha256_file(args.environment_receipt),
        "LVEF_C3_CHECKPOINT": str(args.checkpoint.resolve(strict=True)),
        "LVEF_C3_CHECKPOINT_SHA256": core.sha256_file(args.checkpoint),
        "LVEF_C3_GCLOUD_BINARY": str(args.gcloud_executable.resolve(strict=True)),
        "LVEF_C3_GCLOUD_BINARY_SHA256": core.sha256_file(args.gcloud_executable),
        "LVEF_C3_GCLOUD_RESOLUTION_RECEIPT": str(args.gcloud_resolution_receipt.resolve(strict=True)),
        "LVEF_C3_GCLOUD_RESOLUTION_RECEIPT_SHA256": core.sha256_file(args.gcloud_resolution_receipt),
        "LVEF_C3_CLOUDSDK_CONFIG": str(args.cloudsdk_config.resolve(strict=True)),
        "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT": str(args.cloudsdk_config_receipt.resolve(strict=True)),
        "LVEF_C3_CLOUDSDK_CONFIG_RECEIPT_SHA256": core.sha256_file(args.cloudsdk_config_receipt),
        "LVEF_C3_GCP_BILLING_PROJECT": billing_project,
        "LVEF_C3_BATCH_LEDGER_ROOT": str(roots["initial_ledgers"]),
        "LVEF_C3_DISPATCH_AUTHORIZATION_ROOT": str(authorization_roots["dispatch"]),
        "LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT": str(authorization_roots["download"]),
        "LVEF_C3_EXTRACTION_AUTHORIZATION_ROOT": str(authorization_roots["extraction"]),
        "LVEF_C3_EXTRACTION_WORKERS": str(args.extraction_workers),
        "LVEF_C3_ECHOPRIME_AUTHORIZATION_ROOT": str(authorization_roots["echoprime"]),
        "LVEF_C3_EMBEDDING_BATCH_SIZE": str(args.embedding_batch_size),
        "LVEF_C3_PRESERVATION_AUTHORIZATION_ROOT": str(authorization_roots["preservation"]),
        "LVEF_C3_CACHE_RETIREMENT_AUTHORIZATION_ROOT": str(authorization_roots["cache_retirement"]),
        "LVEF_C3_FINALIZATION_AUTHORIZATION_ROOT": str(authorization_roots["finalization"]),
    }
    execution_environment_path = (
        roots["authority"] / "c3_execution_environment.restricted.env"
    )
    execution_environment_sha = _write_text_no_clobber(
        execution_environment_path, _runtime_environment_text(environment_values)
    )
    ledger_set_sha = hashlib.sha256(
        ("\n".join(sorted(ledger_hashes)) + "\n").encode("ascii")
    ).hexdigest()
    result = {
        "schema_version": 1,
        "artifact_type": "lvef_c3_offline_control_plane_preparation_summary_v1",
        "status": "PASS_OFFLINE_CONTROL_PLANE_PREPARED_UNAUTHORIZED",
        "attempt_id": args.attempt_id,
        "governing_commit": args.governing_commit,
        "production_batches": len(plan["batches"]),
        "selected_studies": requirements.selected_studies,
        "selected_subjects": requirements.selected_subjects,
        "normalized_source_objects": requirements.normalized_source_objects,
        "selected_source_bytes": requirements.selected_source_bytes,
        "batch_plan_sha256": plan_sha,
        "batch_plan_aggregate_sha256": aggregate_plan_sha,
        "runtime_authority_sha256": core.canonical_json_sha256(runtime_authority),
        "initial_ledger_set_sha256": ledger_set_sha,
        "execution_environment_sha256": execution_environment_sha,
        "authorization_scopes_granted": 0,
        "cloud_requests": 0,
        "scheduler_jobs_submitted": 0,
        "object_bodies_downloaded": 0,
        "real_dicom_extraction": False,
        "echoprime_inference": False,
        "model_fitting": False,
        "confirmatory_performance_accessed": False,
        "contains_identifiers": False,
        "contains_source_locators": False,
        "contains_private_project": False,
        "contains_restricted_paths": False,
    }
    _write_json_no_clobber(
        roots["aggregate"] / "lvef_c3_control_plane_preparation.summary.json",
        result,
    )
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--checkout-root", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--selected", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-metadata", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--state-machine-schema", type=Path, required=True)
    parser.add_argument("--resume-ledger-schema", type=Path, required=True)
    parser.add_argument("--gcloud-executable", type=Path, required=True)
    parser.add_argument("--gcloud-resolution-receipt", type=Path, required=True)
    parser.add_argument("--cloudsdk-config", type=Path, required=True)
    parser.add_argument("--cloudsdk-config-receipt", type=Path, required=True)
    parser.add_argument("--python-sha256", required=True)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--extraction-workers", type=int, default=4)
    parser.add_argument("--embedding-batch-size", type=int, default=8)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = prepare(parse_args(argv))
    except (ControlPlanePreparationError, core.OrchestrationError, stages.ProductionStageError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            code = "CONTROL_PLANE_PREPARATION_FAILED"
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 2
    except Exception:
        print(
            json.dumps(
                {"status": "FAIL", "error_code": "CONTROL_PLANE_UNEXPECTED_SANITIZED_EXCEPTION"},
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "production_batches": result["production_batches"],
                "authorization_scopes_granted": 0,
                "cloud_requests": 0,
                "scheduler_jobs_submitted": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
