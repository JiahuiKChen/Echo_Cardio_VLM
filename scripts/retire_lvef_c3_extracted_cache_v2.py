#!/usr/bin/env python3
"""Fail-closed, owner-authorized retirement of one preserved C3 clip cache.

Validation is read-only.  Deletion is reachable only through ``--execute`` and
an exact restricted authorization receipt.  Raw DICOM roots are never accepted
as a target and are revalidated before and after cache retirement.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import stat
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lvef_c3_orchestration_core as core


SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BATCH_RE = re.compile(r"^c3_batch_(?:00[0-9]|01[0-8])$")
ATTEMPT_RE = re.compile(r"^lvef_c3_[a-z0-9][a-z0-9_-]{7,95}$")
AUTH_KEYS = {
    "schema_version", "artifact_type", "status", "authorization_scope",
    "owner_authorized", "owner_authorization_date_utc",
    "batch_id", "attempt_id",
    "authority_sha256", "preservation_receipt_sha256",
    "cache_inventory_sha256", "launch_authority_sha256",
}
INTENT_KEYS = {
    "schema_version", "artifact_type", "status", "batch_id", "attempt_id",
    "governing_commit", "preservation_receipt_sha256",
    "authorization_receipt_sha256", "cache_tree_sha256",
    "raw_dicom_deletion_permitted",
}
STAGED_KEYS = {
    "schema_version", "artifact_type", "status", "batch_id", "attempt_id",
    "governing_commit", "intent_receipt_sha256", "cache_tree_sha256",
    "atomic_same_filesystem_rename_completed", "raw_dicom_deletion_permitted",
}


class CacheRetirementError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CacheRetirementError("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def load_json(path: Path, code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CacheRetirementError(f"{code}_NOT_REGULAR")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except CacheRetirementError:
        raise
    except Exception as exc:
        raise CacheRetirementError(f"{code}_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise CacheRetirementError(f"{code}_NOT_OBJECT")
    return value


def require_owner_private(path: Path, code: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise CacheRetirementError(f"{code}_NOT_REGULAR")
    metadata = path.stat(follow_symlinks=False)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise CacheRetirementError(f"{code}_NOT_OWNER_PRIVATE")


def require_no_symlink_ancestors(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise CacheRetirementError("RETIREMENT_PATH_OUTSIDE_ROOT") from exc
    cursor = root
    if cursor.is_symlink():
        raise CacheRetirementError("RETIREMENT_PATH_SYMLINK_ANCESTOR")
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CacheRetirementError("RETIREMENT_PATH_SYMLINK_ANCESTOR")


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CacheRetirementError("HASH_INPUT_NOT_REGULAR")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_tree_sha256(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise CacheRetirementError("CACHE_ROOT_NOT_REGULAR")
    records: list[str] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in names:
            if (current / name).is_symlink():
                raise CacheRetirementError("CACHE_TREE_SYMLINK")
        for name in filenames:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise CacheRetirementError("CACHE_TREE_NONREGULAR")
            records.append(
                f"{path.relative_to(root).as_posix()}\t{path.stat().st_size}\t{sha256_file(path)}"
            )
    if not records:
        raise CacheRetirementError("CACHE_TREE_EMPTY")
    return hashlib.sha256(("\n".join(sorted(records)) + "\n").encode()).hexdigest()


def validate_preservation_coverage(
    manifest_path: Path, *, production_root: Path,
    required_roots: Sequence[tuple[Path, Path]],
) -> None:
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise CacheRetirementError("PRESERVATION_MANIFEST_NOT_REGULAR")
    with manifest_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        expected_header = ["relative_path", "size_bytes", "sha256", "role"]
        if reader.fieldnames != expected_header or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise CacheRetirementError("PRESERVATION_MANIFEST_SCHEMA_INVALID")
        rows = list(reader)
    by_relative: dict[str, Mapping[str, str]] = {}
    for row in rows:
        relative = str(row["relative_path"])
        if relative in by_relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise CacheRetirementError("PRESERVATION_MANIFEST_PATH_INVALID")
        by_relative[relative] = row
    for actual_root, logical_root in required_roots:
        if actual_root.is_symlink() or not actual_root.is_dir():
            raise CacheRetirementError("PRESERVED_TREE_INVALID")
        for directory, names, filenames in os.walk(actual_root, followlinks=False):
            current = Path(directory)
            if any((current / name).is_symlink() for name in names):
                raise CacheRetirementError("PRESERVED_TREE_SYMLINK")
            for name in filenames:
                path = current / name
                if path.is_symlink() or not path.is_file():
                    raise CacheRetirementError("PRESERVED_TREE_NONREGULAR")
                logical_path = logical_root / path.relative_to(actual_root)
                relative = logical_path.relative_to(production_root).as_posix()
                row = by_relative.get(relative)
                if (
                    row is None
                    or int(row["size_bytes"]) != path.stat().st_size
                    or row["sha256"] != sha256_file(path)
                ):
                    raise CacheRetirementError("PRESERVATION_TREE_COVERAGE_MISMATCH")


def validate_gate(
    *, contract_path: Path, plan_path: Path, environment_receipt: Path,
    production_root: Path, attempt_id: str, batch_id: str,
    governing_commit: str, final_ledger_path: Path,
    preservation_receipt_path: Path, authorization_receipt_path: Path,
    launch_authority_sha256: str,
    require_authorization: bool,
) -> dict[str, Any]:
    if (
        not ATTEMPT_RE.fullmatch(attempt_id)
        or not BATCH_RE.fullmatch(batch_id)
        or not COMMIT_RE.fullmatch(governing_commit)
    ):
        raise CacheRetirementError("IDENTITY_ARGUMENT_INVALID")
    expected_prefix = Path("/restricted/projectnb")
    if production_root.is_symlink() or not production_root.is_dir():
        raise CacheRetirementError("PRODUCTION_ROOT_INVALID")
    try:
        production_root.resolve().relative_to(expected_prefix)
    except ValueError as exc:
        raise CacheRetirementError("PRODUCTION_ROOT_OUTSIDE_PROJECTNB") from exc
    # Only extracted NPZ clip derivatives are owner-retirable.  DICOM audit,
    # extraction manifests, summaries, and transition receipts remain in the
    # parent directory as permanent provenance.
    cache_root = (
        production_root / "attempts" / attempt_id / "extracted_cache" /
        batch_id / "dicom_extraction" / "clips"
    )
    raw_root = production_root / "attempts" / attempt_id / "raw" / batch_id / "objects"
    require_no_symlink_ancestors(cache_root, production_root)
    require_no_symlink_ancestors(raw_root, production_root)
    if cache_root == raw_root or "raw" in cache_root.parts[-5:]:
        raise CacheRetirementError("RAW_TARGET_PROHIBITED")
    contract = core.load_orchestration_contract(contract_path)
    plan = core.load_strict_json(plan_path)
    requirements = core.production_requirements(contract)
    plan_sha = core.validate_batch_plan(plan, requirements=requirements)
    planned = next((row for row in plan["batches"] if row["batch_id"] == batch_id), None)
    if planned is None:
        raise CacheRetirementError("BATCH_NOT_PLANNED")
    ledger = core.load_strict_json(final_ledger_path)
    expected_authority = core.validate_ledger_against_current_runtime(
        ledger,
        plan=plan,
        requirements=requirements,
        contract=contract,
        contract_path=contract_path,
        governing_commit=governing_commit,
        environment_receipt_sha256=sha256_file(environment_receipt),
        batch_id=batch_id,
    )
    if (
        ledger.get("attempt_id") != attempt_id
        or ledger["batches"][batch_id]["state"] != "CACHE_RETIREMENT_ELIGIBLE"
    ):
        raise CacheRetirementError("BATCH_NOT_CACHE_RETIREMENT_ELIGIBLE")
    preservation = load_json(preservation_receipt_path, "PRESERVATION_RECEIPT")
    if (
        preservation.get("status") != "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"
        or preservation.get("artifact_type")
        != "lvef_c3_batch_preservation_eligibility_receipt_v2"
        or preservation.get("attempt_id") != attempt_id
        or preservation.get("batch_id") != batch_id
        or preservation.get("preservation_gate_passed") is not True
        or preservation.get("raw_dicoms_retained") is not True
        or preservation.get("extracted_cache_retired") is not False
        or preservation.get("batch_plan_sha256") != plan_sha
        or preservation.get("governing_commit") != governing_commit
    ):
        raise CacheRetirementError("PRESERVATION_AUTHORITY_INVALID")
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise CacheRetirementError("RAW_RETENTION_ROOT_INVALID")
    raw_files = [path for path in raw_root.iterdir() if path.is_file() and not path.is_symlink()]
    if len(raw_files) != planned["n_objects"]:
        raise CacheRetirementError("RAW_RETENTION_COUNT_MISMATCH")
    preservation_manifest = preservation_receipt_path.parent / "batch_preservation_manifest.restricted.tsv"
    if sha256_file(preservation_manifest) != preservation.get("preservation_manifest_sha256"):
        raise CacheRetirementError("PRESERVATION_MANIFEST_HASH_MISMATCH")
    authorization = None
    intent_path = preservation_receipt_path.parent / "cache_retirement_intent.restricted.json"
    staged_path = preservation_receipt_path.parent / "cache_atomically_staged.restricted.json"
    retirement_staging = None
    intent = None
    if require_authorization:
        require_owner_private(authorization_receipt_path, "CACHE_AUTHORIZATION")
        authorization = load_json(authorization_receipt_path, "CACHE_AUTHORIZATION")
        authorization_sha = sha256_file(authorization_receipt_path)
        retirement_staging = (
            production_root
            / "attempts"
            / attempt_id
            / "retired_cache_staging"
            / f"{batch_id}.{authorization_sha}.pending"
        )
        require_no_symlink_ancestors(retirement_staging, production_root)
        if intent_path.exists() or intent_path.is_symlink():
            intent = load_json(intent_path, "CACHE_RETIREMENT_INTENT")
            if set(intent) != INTENT_KEYS:
                raise CacheRetirementError("CACHE_RETIREMENT_INTENT_SCHEMA_MISMATCH")
            tree_sha = str(intent.get("cache_tree_sha256"))
        else:
            tree_sha = cache_tree_sha256(cache_root)
    else:
        tree_sha = cache_tree_sha256(cache_root)
    if intent is None:
        validate_preservation_coverage(
            preservation_manifest,
            production_root=production_root,
            required_roots=((cache_root, cache_root), (raw_root, raw_root)),
        )
    else:
        validate_preservation_coverage(
            preservation_manifest,
            production_root=production_root,
            required_roots=((raw_root, raw_root),),
        )
    if require_authorization:
        expected = {
            "schema_version": 2,
            "artifact_type": "lvef_c3_cache_retirement_owner_authorization_v2",
            "status": "AUTHORIZED_EXTRACTED_CACHE_RETIREMENT",
            "authorization_scope": "EXTRACTED_CACHE_RETIREMENT",
            "owner_authorized": True,
            "batch_id": batch_id,
            "attempt_id": attempt_id,
            "authority_sha256": core.canonical_json_sha256(ledger["authority"]),
            "preservation_receipt_sha256": sha256_file(preservation_receipt_path),
            "cache_inventory_sha256": tree_sha,
            "launch_authority_sha256": launch_authority_sha256,
        }
        if set(authorization) != AUTH_KEYS or any(
            authorization.get(key) != value for key, value in expected.items()
        ):
            raise CacheRetirementError("CACHE_AUTHORIZATION_MISMATCH")
        retirement_gate = core.evaluate_cache_retirement(
            ledger,
            batch_id=batch_id,
            target_kind="extracted_cache",
            contract=contract,
            owner_authorization=authorization,
            expected_launch_authority_sha256=launch_authority_sha256,
        )
        if retirement_gate.get("authorized") is not True:
            raise CacheRetirementError("CACHE_RETIREMENT_POLICY_GATE_BLOCKED")
        expected_intent = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_cache_retirement_intent_v1",
            "status": "AUTHORIZED_INTENT_RECORDED_NOT_RETIRED",
            "batch_id": batch_id,
            "attempt_id": attempt_id,
            "governing_commit": governing_commit,
            "preservation_receipt_sha256": sha256_file(preservation_receipt_path),
            "authorization_receipt_sha256": authorization_sha,
            "cache_tree_sha256": tree_sha,
            "raw_dicom_deletion_permitted": False,
        }
        if intent is not None and intent != expected_intent:
            raise CacheRetirementError("CACHE_RETIREMENT_INTENT_MISMATCH")
        expected_staged = {
            "schema_version": 1,
            "artifact_type": "lvef_c3_cache_atomically_staged_v1",
            "status": "CACHE_ATOMICALLY_STAGED",
            "batch_id": batch_id,
            "attempt_id": attempt_id,
            "governing_commit": governing_commit,
            "intent_receipt_sha256": sha256_file(intent_path) if intent is not None else None,
            "cache_tree_sha256": tree_sha,
            "atomic_same_filesystem_rename_completed": True,
            "raw_dicom_deletion_permitted": False,
        }
        staged = None
        if staged_path.exists() or staged_path.is_symlink():
            staged = load_json(staged_path, "CACHE_ATOMICALLY_STAGED_RECEIPT")
            if set(staged) != STAGED_KEYS or staged != expected_staged:
                raise CacheRetirementError("CACHE_ATOMICALLY_STAGED_RECEIPT_MISMATCH")
        cache_exists = cache_root.exists() or cache_root.is_symlink()
        staging_exists = retirement_staging.exists() or retirement_staging.is_symlink()
        if staged is not None and cache_exists:
            raise CacheRetirementError("STAGED_RECEIPT_WITH_ACTIVE_CACHE")
        if intent is not None and staged is None and not cache_exists and not staging_exists:
            raise CacheRetirementError("ATOMIC_STAGING_EVIDENCE_MISSING")
        if intent is not None and staged is None and staging_exists:
            if cache_tree_sha256(retirement_staging) != tree_sha:
                raise CacheRetirementError("UNPROVEN_PARTIAL_STAGING")
            validate_preservation_coverage(
                preservation_manifest,
                production_root=production_root,
                required_roots=((retirement_staging, cache_root),),
            )
    if expected_authority["checkpoint_sha256"] != preservation["checkpoint_sha256"]:
        raise CacheRetirementError("CHECKPOINT_AUTHORITY_MISMATCH")
    return {
        "cache_root": cache_root,
        "raw_root": raw_root,
        "cache_tree_sha256": tree_sha,
        "retirement_staging": retirement_staging,
        "intent_path": intent_path,
        "intent": intent,
        "expected_intent": expected_intent if require_authorization else None,
        "staged_path": staged_path,
        "staged": staged if require_authorization else None,
        "expected_staged": expected_staged if require_authorization else None,
        "ledger": ledger,
        "preservation": preservation,
        "authorization": authorization if require_authorization else None,
    }


def _delete_cache_tree(root: Path) -> None:
    """Delete only the already validated exact cache tree, never following links."""
    for entry in os.scandir(root):
        path = Path(entry.path)
        if entry.is_symlink():
            raise CacheRetirementError("CACHE_TREE_SYMLINK")
        if entry.is_dir(follow_symlinks=False):
            _delete_cache_tree(path)
        elif entry.is_file(follow_symlinks=False):
            path.unlink()
        else:
            raise CacheRetirementError("CACHE_TREE_NONREGULAR")
    root.rmdir()


def classify_retirement_state(
    *, cache_exists: bool, staging_exists: bool, intent_exists: bool,
    staged_receipt_exists: bool = False,
) -> str:
    if cache_exists and staging_exists:
        raise CacheRetirementError("CACHE_AND_RETIREMENT_STAGING_BOTH_EXIST")
    if not intent_exists and not cache_exists:
        raise CacheRetirementError("CACHE_MISSING_BEFORE_RETIREMENT_INTENT")
    if not intent_exists:
        return "READY_TO_RECORD_INTENT"
    if staged_receipt_exists and cache_exists:
        raise CacheRetirementError("STAGED_RECEIPT_WITH_ACTIVE_CACHE")
    if cache_exists:
        return "INTENT_RECORDED_READY_TO_RENAME"
    if staging_exists:
        return (
            "RENAMED_OR_PARTIALLY_CLEANED_RESUME"
            if staged_receipt_exists
            else "FULL_STAGING_REQUIRES_HASH_AND_STAGED_RECEIPT"
        )
    if not staged_receipt_exists:
        raise CacheRetirementError("ATOMIC_STAGING_EVIDENCE_MISSING")
    return "PHYSICAL_RETIREMENT_COMPLETE_READY_TO_FINALIZE"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--environment-receipt", type=Path, required=True)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--governing-commit", required=True)
    parser.add_argument("--final-ledger", type=Path, required=True)
    parser.add_argument("--preservation-receipt", type=Path, required=True)
    parser.add_argument("--authorization-receipt", type=Path, required=True)
    parser.add_argument("--launch-authority-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.validate_only == args.execute:
        raise CacheRetirementError("EXACTLY_ONE_MODE_REQUIRED")
    context = validate_gate(
        contract_path=args.contract,
        plan_path=args.plan,
        environment_receipt=args.environment_receipt,
        production_root=args.production_root,
        attempt_id=args.attempt_id,
        batch_id=args.batch_id,
        governing_commit=args.governing_commit,
        final_ledger_path=args.final_ledger,
        preservation_receipt_path=args.preservation_receipt,
        authorization_receipt_path=args.authorization_receipt,
        launch_authority_sha256=args.launch_authority_sha256,
        require_authorization=args.execute,
    )
    if args.execute:
        cache_root = context["cache_root"]
        raw_root = context["raw_root"]
        staging = context["retirement_staging"]
        intent_path = context["intent_path"]
        expected_intent = context["expected_intent"]
        if context["intent"] is None:
            core.atomic_write_json_no_clobber(
                intent_path, expected_intent, attempt_id=args.attempt_id
            )
        elif context["intent"] != expected_intent:
            raise CacheRetirementError("CACHE_RETIREMENT_INTENT_MISMATCH")
        cache_exists = cache_root.exists() or cache_root.is_symlink()
        staging_exists = staging.exists() or staging.is_symlink()
        classify_retirement_state(
            cache_exists=cache_exists,
            staging_exists=staging_exists,
            intent_exists=True,
            staged_receipt_exists=context["staged"] is not None,
        )
        if cache_exists:
            if cache_root.is_symlink():
                raise CacheRetirementError("CACHE_ROOT_SYMLINK")
            if staging.parent.is_symlink():
                raise CacheRetirementError("RETIREMENT_STAGING_PARENT_SYMLINK")
            staging.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.rename(cache_root, staging)
            staging_exists = True
        expected_staged = dict(context["expected_staged"])
        expected_staged["intent_receipt_sha256"] = sha256_file(intent_path)
        staged_path = context["staged_path"]
        if context["staged"] is None:
            if not staging_exists or cache_tree_sha256(staging) != context["cache_tree_sha256"]:
                raise CacheRetirementError("ATOMIC_STAGING_HASH_NOT_PROVEN")
            core.atomic_write_json_no_clobber(
                staged_path, expected_staged, attempt_id=args.attempt_id
            )
        elif context["staged"] != expected_staged:
            raise CacheRetirementError("CACHE_ATOMICALLY_STAGED_RECEIPT_MISMATCH")
        if staging_exists:
            if staging.is_symlink() or not staging.is_dir():
                raise CacheRetirementError("RETIREMENT_STAGING_INVALID")
            _delete_cache_tree(staging)
        if cache_root.exists() or cache_root.is_symlink() or staging.exists() or staging.is_symlink():
            raise CacheRetirementError("CACHE_RETIREMENT_POSTCONDITION_FAILED")
        if not raw_root.is_dir() or raw_root.is_symlink():
            raise CacheRetirementError("RAW_RETENTION_POSTCONDITION_FAILED")
        preservation = dict(context["preservation"])
        final_receipt = {
            **preservation,
            "artifact_type": "lvef_c3_batch_finalization_receipt_v2",
            "status": "PASS_BATCH_FINALIZED",
            "extracted_cache_retired": True,
            "cache_retirement_authorization_sha256": sha256_file(
                args.authorization_receipt
            ),
            "cache_tree_sha256": context["cache_tree_sha256"],
            "cache_atomically_staged_receipt_sha256": sha256_file(
                context["staged_path"]
            ),
            "cache_retirement_script_sha256": sha256_file(Path(__file__).resolve()),
        }
        final_receipt_path = (
            args.production_root
            / "attempts"
            / args.attempt_id
            / "batches"
            / args.batch_id
            / "preservation"
            / "batch_finalization_receipt.restricted.json"
        )
        if final_receipt_path.exists() or final_receipt_path.is_symlink():
            if core.load_strict_json(final_receipt_path) != final_receipt:
                raise CacheRetirementError("FINALIZATION_RECEIPT_RECOVERY_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                final_receipt_path, final_receipt, attempt_id=args.attempt_id
            )
        ledger = context["ledger"]
        batch = ledger["batches"][args.batch_id]
        transition = {
            "schema_version": 2,
            "receipt_type": "lvef_c3_state_transition_v2",
            "attempt_id": args.attempt_id,
            "batch_id": args.batch_id,
            "from_state": "CACHE_RETIREMENT_ELIGIBLE",
            "to_state": "FINALIZED",
            "status": "PASS",
            "authority": ledger["authority"],
            "input_receipt_sha256": [batch["events"][-1]["receipt_sha256"]],
            "output_manifest_sha256": sha256_file(final_receipt_path),
        }
        updated = core.apply_transition(ledger, transition)
        transition_path = final_receipt_path.parent / "cache_retirement_finalized.restricted.json"
        if transition_path.exists() or transition_path.is_symlink():
            if core.load_strict_json(transition_path) != transition:
                raise CacheRetirementError("RETIREMENT_TRANSITION_RECOVERY_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                transition_path, transition, attempt_id=args.attempt_id
            )
        final_ledger_path = final_receipt_path.parents[1] / "final_resume_ledger.restricted.json"
        if final_ledger_path.exists() or final_ledger_path.is_symlink():
            if core.load_strict_json(final_ledger_path) != updated:
                raise CacheRetirementError("FINAL_LEDGER_RECOVERY_MISMATCH")
        else:
            core.atomic_write_json_no_clobber(
                final_ledger_path, updated, attempt_id=args.attempt_id
            )
    print("C3_CACHE_RETIREMENT_GATE=PASS")
    print(f"CACHE_RETIREMENT_EXECUTED={'YES' if args.execute else 'NO'}")
    print("RAW_DICOM_DELETION=NO")
    return 0


def guarded_main(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except CacheRetirementError as exc:
        print(f"C3_CACHE_RETIREMENT_GATE=BLOCKED_{exc.code}")
        return 78
    except Exception:
        print("C3_CACHE_RETIREMENT_GATE=BLOCKED_UNEXPECTED_SANITIZED_EXCEPTION")
        return 78


if __name__ == "__main__":
    raise SystemExit(guarded_main())
