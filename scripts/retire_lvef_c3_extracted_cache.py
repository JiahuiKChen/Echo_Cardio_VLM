#!/usr/bin/env python3
"""Fail-closed rolling extracted-cache retirement helper for C3 batches."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from typing import Any, Mapping, Sequence

import yaml

from validate_lvef_c3_execution_contract import validate_contract


BATCH_RE = re.compile(r"^c3_batch_[0-9]{3}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_PRESERVED_ROLES = {
    "source_batch_manifest",
    "download_manifest",
    "dicom_header_manifest",
    "extraction_manifest",
    "extracted_cache_manifest",
    "clip_embedding_store",
    "clip_embedding_manifest",
    "study_embedding_store",
    "study_embedding_manifest",
    "safety_gate",
    "environment_receipt",
    "command_config_manifest",
}


class RetirementError(ValueError):
    pass


def _load_json(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RetirementError("REQUIRED_JSON_NOT_REGULAR_FILE")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise RetirementError("REQUIRED_JSON_NOT_MAPPING")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_tree_records(cache_path: Path, marker_name: str) -> list[dict[str, Any]]:
    """Inventory exact cache-relative files; reject links and special files."""
    records: list[dict[str, Any]] = []
    for path in sorted(cache_path.rglob("*"), key=lambda value: value.relative_to(cache_path).as_posix()):
        if path.is_symlink():
            raise RetirementError("CACHE_TREE_CONTAINS_SYMLINK")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RetirementError("CACHE_TREE_CONTAINS_SPECIAL_FILE")
        relative = path.relative_to(cache_path).as_posix()
        if relative == marker_name:
            continue
        records.append(
            {
                "relative_path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if not records:
        raise RetirementError("CACHE_TREE_HAS_NO_RETIRABLE_FILES")
    return records


def _records_inventory_sha256(records: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(str(record["relative_path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["size_bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(record["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _cache_inventory_sha256(cache_path: Path, marker_name: str) -> str:
    return _records_inventory_sha256(_cache_tree_records(cache_path, marker_name))


def _safe_manifest_relative(value: Any) -> str:
    text = str(value)
    path = PurePosixPath(text)
    if (
        not text
        or text != path.as_posix()
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RetirementError("MANIFEST_RELATIVE_PATH_INVALID")
    return text


def _validate_cache_manifest(
    path: Path,
    *,
    batch_id: str,
    cache_path: Path,
    marker_name: str,
) -> str:
    manifest = _load_json(path)
    if (
        set(manifest) != {
            "schema_version", "manifest_type", "batch_id", "n_files",
            "total_bytes", "cache_inventory_sha256", "files",
        }
        or manifest.get("schema_version") != 1
        or manifest.get("manifest_type") != "c3_extracted_cache_manifest_v1"
        or manifest.get("batch_id") != batch_id
        or not isinstance(manifest.get("files"), list)
    ):
        raise RetirementError("CACHE_MANIFEST_SCHEMA_INVALID")
    declared: list[dict[str, Any]] = []
    for row in manifest["files"]:
        if not isinstance(row, Mapping) or set(row) != {"relative_path", "size_bytes", "sha256"}:
            raise RetirementError("CACHE_MANIFEST_FILE_SCHEMA_INVALID")
        relative = _safe_manifest_relative(row["relative_path"])
        size = row["size_bytes"]
        digest = str(row["sha256"])
        if not relative.endswith(".npz") or not isinstance(size, int) or size < 0 or not SHA256_RE.fullmatch(digest):
            raise RetirementError("CACHE_MANIFEST_FILE_IDENTITY_INVALID")
        declared.append({"relative_path": relative, "size_bytes": size, "sha256": digest})
    declared = sorted(declared, key=lambda row: row["relative_path"])
    if len({row["relative_path"] for row in declared}) != len(declared):
        raise RetirementError("CACHE_MANIFEST_DUPLICATE_PATH")
    actual = _cache_tree_records(cache_path, marker_name)
    if declared != actual:
        raise RetirementError("CACHE_TREE_DOES_NOT_EQUAL_MANIFEST")
    inventory_sha = _records_inventory_sha256(actual)
    if (
        manifest.get("n_files") != len(actual)
        or manifest.get("total_bytes") != sum(int(row["size_bytes"]) for row in actual)
        or manifest.get("cache_inventory_sha256") != inventory_sha
    ):
        raise RetirementError("CACHE_MANIFEST_TOTAL_OR_HASH_MISMATCH")
    return inventory_sha


def _validate_preservation_manifest(
    path: Path, *, batch_id: str, deletion_scope: Path
) -> None:
    manifest = _load_json(path)
    if (
        set(manifest) != {"schema_version", "manifest_type", "batch_id", "status", "files"}
        or manifest.get("schema_version") != 1
        or manifest.get("manifest_type") != "c3_batch_preservation_manifest_v1"
        or manifest.get("batch_id") != batch_id
        or manifest.get("status") != "PASS"
        or not isinstance(manifest.get("files"), list)
    ):
        raise RetirementError("PRESERVATION_MANIFEST_SCHEMA_INVALID")
    roles: set[str] = set()
    paths: set[str] = set()
    root = path.parent.resolve(strict=True)
    for row in manifest["files"]:
        if not isinstance(row, Mapping) or set(row) != {"role", "relative_path", "size_bytes", "sha256"}:
            raise RetirementError("PRESERVATION_FILE_SCHEMA_INVALID")
        role = str(row["role"])
        relative = _safe_manifest_relative(row["relative_path"])
        size = row["size_bytes"]
        digest = str(row["sha256"])
        if role in roles or relative in paths or not isinstance(size, int) or size < 0 or not SHA256_RE.fullmatch(digest):
            raise RetirementError("PRESERVATION_FILE_IDENTITY_INVALID")
        candidate = root / relative
        resolved = candidate.resolve(strict=True)
        if root not in resolved.parents or candidate.is_symlink() or not candidate.is_file():
            raise RetirementError("PRESERVATION_FILE_OUTSIDE_ROOT_OR_INVALID")
        resolved_deletion_scope = deletion_scope.resolve(strict=True)
        if resolved == resolved_deletion_scope or resolved_deletion_scope in resolved.parents:
            raise RetirementError("PRESERVATION_FILE_INSIDE_DELETION_SCOPE")
        if candidate.stat().st_size != size or _sha256_file(candidate) != digest:
            raise RetirementError("PRESERVATION_FILE_HASH_MISMATCH")
        roles.add(role)
        paths.add(relative)
    if not REQUIRED_PRESERVED_ROLES.issubset(roles):
        raise RetirementError("PRESERVATION_REQUIRED_ROLE_MISSING")


def evaluate_retirement(
    *,
    contract_path: Path,
    owner_authorization_path: Path | None,
    batch_id: str,
    cache_path: Path,
    gate_report_path: Path,
    target_kind: str,
    batch_manifest_path: Path | None = None,
    preservation_manifest_path: Path | None = None,
) -> dict[str, Any]:
    if target_kind != "extracted_cache":
        raise RetirementError("RAW_DICOM_DELETION_PROHIBITED")
    if not BATCH_RE.fullmatch(batch_id):
        raise RetirementError("INVALID_BATCH_ID")
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    validation = validate_contract(contract_path, owner_authorization_path)
    storage = contract["storage"]
    retirement = contract["cache_retirement"]
    cache_root = Path(str(storage["extracted_cache_root"])).resolve(strict=False)
    raw_root = Path(str(storage["raw_dicom_root"])).resolve(strict=False)
    candidate = cache_path.resolve(strict=False)
    expected = cache_root / batch_id
    if candidate != expected:
        raise RetirementError("CACHE_PATH_NOT_EXACT_LOCKED_BATCH_ROOT")
    if candidate == raw_root or raw_root in candidate.parents:
        raise RetirementError("RAW_DICOM_DELETION_PROHIBITED")
    if cache_path.is_symlink() or not cache_path.is_dir():
        raise RetirementError("CACHE_PATH_NOT_REGULAR_DIRECTORY")
    marker_path = cache_path / str(retirement["require_marker_file"])
    marker = _load_json(marker_path)
    gates = _load_json(gate_report_path)
    if batch_manifest_path is None or preservation_manifest_path is None:
        raise RetirementError("BATCH_AND_PRESERVATION_MANIFESTS_REQUIRED")
    for authority_path in (batch_manifest_path, preservation_manifest_path, gate_report_path):
        if authority_path.is_symlink() or not authority_path.is_file():
            raise RetirementError("RETIREMENT_AUTHORITY_NOT_REGULAR_FILE")
        resolved_authority = authority_path.resolve(strict=True)
        if resolved_authority == candidate or candidate in resolved_authority.parents:
            raise RetirementError("RETIREMENT_AUTHORITY_INSIDE_DELETION_SCOPE")
    required = set(retirement["required_batch_gates"])
    observed = gates.get("gates")
    if gates.get("batch_id") != batch_id or not isinstance(observed, Mapping):
        raise RetirementError("BATCH_GATE_REPORT_MISMATCH")
    if set(observed) != required or any(observed[name] != "PASS" for name in required):
        raise RetirementError("BATCH_RETIREMENT_GATES_NOT_ALL_PASS")
    contract_sha = validation["contract_sha256"]
    source_sha = contract["authority"]["frozen_selected_source_manifest_sha256"]
    batch_manifest_sha = _sha256_file(batch_manifest_path)
    preservation_manifest_sha = _sha256_file(preservation_manifest_path)
    marker_name = str(retirement["require_marker_file"])
    cache_inventory_sha = _validate_cache_manifest(
        batch_manifest_path,
        batch_id=batch_id,
        cache_path=cache_path,
        marker_name=marker_name,
    )
    _validate_preservation_manifest(
        preservation_manifest_path,
        batch_id=batch_id,
        deletion_scope=cache_path,
    )
    if marker.get("batch_id") != batch_id:
        raise RetirementError("CACHE_MARKER_BATCH_MISMATCH")
    if marker.get("contract_sha256") != contract_sha:
        raise RetirementError("CACHE_MARKER_CONTRACT_SHA256_MISMATCH")
    if marker.get("selected_source_manifest_sha256") != source_sha:
        raise RetirementError("CACHE_MARKER_SOURCE_SHA256_MISMATCH")
    bindings = {
        "contract_sha256": contract_sha,
        "selected_source_manifest_sha256": source_sha,
        "batch_manifest_sha256": batch_manifest_sha,
        "preservation_manifest_sha256": preservation_manifest_sha,
        "cache_inventory_sha256": cache_inventory_sha,
    }
    for name, expected_value in bindings.items():
        if gates.get(name) != expected_value:
            raise RetirementError(f"BATCH_GATE_{name.upper()}_MISMATCH")
        if marker.get(name) != expected_value:
            raise RetirementError(f"CACHE_MARKER_{name.upper()}_MISMATCH")
    if gates.get("raw_dicoms_retained") is not True:
        raise RetirementError("RAW_DICOM_RETENTION_NOT_CONFIRMED")
    if validation["execution_authorized"] is not True:
        raise RetirementError("FULL_C3_EXECUTION_NOT_AUTHORIZED")
    if contract["authorization"]["cache_retirement"] is not True:
        raise RetirementError("CACHE_RETIREMENT_NOT_OWNER_AUTHORIZED")
    return {
        "schema_version": 1,
        "status": "PASS_RETIREMENT_GATES",
        "batch_id": batch_id,
        "target_kind": target_kind,
        "contract_sha256": contract_sha,
        "raw_dicoms_retained": True,
        "all_required_batch_gates_passed": True,
        "cache_path_exactly_scoped": True,
        "execution_permitted": True,
        "batch_manifest_sha256": batch_manifest_sha,
        "preservation_manifest_sha256": preservation_manifest_sha,
        "cache_inventory_sha256": cache_inventory_sha,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--owner-authorization", type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--cache-path", type=Path, required=True)
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument("--batch-manifest", type=Path, required=True)
    parser.add_argument("--preservation-manifest", type=Path, required=True)
    parser.add_argument("--target-kind", choices=("extracted_cache", "raw_dicom"), required=True)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = evaluate_retirement(
            contract_path=args.contract,
            owner_authorization_path=args.owner_authorization,
            batch_id=args.batch_id,
            cache_path=args.cache_path,
            gate_report_path=args.gate_report,
            target_kind=args.target_kind,
            batch_manifest_path=args.batch_manifest,
            preservation_manifest_path=args.preservation_manifest,
        )
        if args.execute:
            confirmation_name = yaml.safe_load(args.contract.read_text(encoding="utf-8"))[
                "cache_retirement"
            ]["execute_confirmation_environment_variable"]
            expected = f"RETIRE_EXTRACTED_CACHE:{args.batch_id}:{result['contract_sha256'][:12]}"
            if os.environ.get(confirmation_name) != expected:
                raise RetirementError("EXPLICIT_CACHE_RETIREMENT_CONFIRMATION_MISSING")
            marker_name = yaml.safe_load(args.contract.read_text(encoding="utf-8"))[
                "cache_retirement"
            ]["require_marker_file"]
            if _cache_inventory_sha256(args.cache_path, marker_name) != result["cache_inventory_sha256"]:
                raise RetirementError("CACHE_CHANGED_AFTER_RETIREMENT_VALIDATION")
            shutil.rmtree(args.cache_path)
            result["status"] = "PASS_CACHE_RETIRED"
            result["cache_deleted"] = True
        else:
            result["status"] = "PASS_DRY_RUN_ONLY"
            result["cache_deleted"] = False
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(json.dumps({"status": "BLOCKED", "error_code": str(exc), "cache_deleted": False}, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
