#!/usr/bin/env python3
"""Read-only SCC filesystem, quota, mount, and migration inventory."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

import yaml

from lvef_multitask_analysis_modes import (
    bind_approved_restricted_path,
    load_policy as load_safe_export_policy,
)


class StorageAuditError(ValueError):
    pass


def _run(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8", errors="replace")).hexdigest(),
    }


def _findmnt(path: Path) -> dict[str, Any]:
    result = _run(["findmnt", "--json", "--target", str(path)])
    if result["returncode"] != 0:
        return {"status": "UNAVAILABLE", "error_digest": result["stderr_sha256"]}
    try:
        payload = json.loads(result["stdout"])
        filesystems = payload.get("filesystems") or []
        row = filesystems[0]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        return {"status": "UNPARSEABLE"}
    return {
        "status": "PASS",
        "source": row.get("source"),
        "target": row.get("target"),
        "fstype": row.get("fstype"),
        "options": row.get("options"),
        "is_bind_mount": "bind" in str(row.get("options", "")).split(","),
    }


def _du_inventory(path: Path, depth: int) -> list[tuple[Path, int]]:
    result = _run(["du", "-x", "-B1", f"--max-depth={depth}", str(path)])
    if result["returncode"] != 0:
        raise StorageAuditError("DU_INVENTORY_FAILED")
    rows: list[tuple[Path, int]] = []
    for line in result["stdout"].splitlines():
        size, separator, raw_path = line.partition("\t")
        if not separator or not size.isdigit():
            raise StorageAuditError("DU_OUTPUT_UNPARSEABLE")
        rows.append((Path(raw_path), int(size)))
    return rows


def _classification(path: Path, rules: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    text = path.as_posix()
    for rule in rules:
        if fnmatch.fnmatch(text.lower(), str(rule["pattern"]).lower()):
            return str(rule["recovery_class"]), str(rule["migration_disposition"])
    return "restricted_irreplaceability_unresolved", "requires_approved_backup_copy_or_owner_adjudication"


def _symlinks(path: Path, depth: int) -> list[str]:
    found: list[str] = []
    base_depth = len(path.parts)
    for current, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        if len(current_path.parts) - base_depth >= depth:
            directories[:] = []
        for name in list(directories) + files:
            candidate = current_path / name
            if candidate.is_symlink():
                found.append(str(candidate))
    return found


def audit(policy: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    storage_policy = policy["storage_inventory"]
    rules = policy["recovery_classification_rules"]
    depth = int(storage_policy["maximum_inventory_depth"])
    roots_detail: list[dict[str, Any]] = []
    roots_safe: list[dict[str, Any]] = []
    for spec in storage_policy["roots"]:
        label = str(spec["label"])
        path = Path(str(spec["path"]))
        exists = path.exists()
        mount = _findmnt(path) if exists else {"status": "PATH_ABSENT"}
        entries: list[dict[str, Any]] = []
        class_totals: dict[str, int] = {}
        root_allocated_bytes = 0
        direct_child_allocated_bytes = 0
        symlinks: list[str] = []
        statvfs: dict[str, int] | None = None
        device: int | None = None
        if exists:
            stat = path.stat()
            device = stat.st_dev
            values = os.statvfs(path)
            statvfs = {
                "capacity_bytes": values.f_blocks * values.f_frsize,
                "available_bytes": values.f_bavail * values.f_frsize,
                "free_bytes": values.f_bfree * values.f_frsize,
            }
            for item_path, size in _du_inventory(path, depth):
                recovery, disposition = _classification(item_path, rules)
                entries.append(
                    {
                        "path": str(item_path),
                        "size_bytes": size,
                        "recovery_class": recovery,
                        "migration_disposition": disposition,
                    }
                )
                relative_depth = len(item_path.parts) - len(path.parts)
                if relative_depth == 0:
                    root_allocated_bytes = size
                elif relative_depth == 1:
                    direct_child_allocated_bytes += size
                    class_totals[recovery] = class_totals.get(recovery, 0) + size
            symlinks = _symlinks(path, depth)
        roots_detail.append(
            {
                "label": label,
                "path": str(path),
                "exists": exists,
                "device": device,
                "mount": mount,
                "statvfs": statvfs,
                "symlinks": symlinks,
                "inventory": entries,
            }
        )
        roots_safe.append(
            {
                "label": label,
                "exists": exists,
                "device": device,
                "mount_status": mount.get("status"),
                "filesystem_type": mount.get("fstype"),
                "is_bind_mount": mount.get("is_bind_mount"),
                "capacity_bytes": statvfs["capacity_bytes"] if statvfs else None,
                "available_bytes": statvfs["available_bytes"] if statvfs else None,
                "symlink_count": len(symlinks),
                "inventory_entry_count": len(entries),
                "root_allocated_bytes": root_allocated_bytes,
                "direct_child_allocated_bytes": direct_child_allocated_bytes,
                "unattributed_root_files_or_overhead_bytes": max(
                    0, root_allocated_bytes - direct_child_allocated_bytes
                ),
                "bytes_by_recovery_class_nonoverlapping": dict(sorted(class_totals.items())),
            }
        )
    existing_devices = [row["device"] for row in roots_safe if row["exists"]]
    if shutil.which("pquota"):
        quota_command = ["pquota", "-u", "mimicecho"]
        quota_scope = "PROJECT_GROUP"
    else:
        quota_command = ["quota", "-s"]
        quota_scope = "HOME_FALLBACK_NOT_PROJECT_AUTHORITY"
    quota_result = _run(quota_command)
    scheduler_paths = []
    for name in storage_policy["scheduler_path_environment_variables"]:
        value = os.environ.get(str(name))
        scheduler_paths.append(
            {
                "environment_variable": name,
                "is_set": bool(value),
                "path": value,
                "mount": _findmnt(Path(value)) if value and Path(value).exists() else None,
            }
        )
    detail = {
        "schema_version": 1,
        "status": "PASS_READ_ONLY",
        "roots": roots_detail,
        "scheduler_paths": scheduler_paths,
        "quota_command": {
            "command": quota_command,
            "scope": quota_scope,
            "returncode": quota_result["returncode"],
            "stdout": quota_result["stdout"],
            "stderr_bytes": quota_result["stderr_bytes"],
            "stderr_sha256": quota_result["stderr_sha256"],
        },
        "administrative_questions": storage_policy["administrative_questions"],
        "files_moved": 0,
        "files_deleted": 0,
    }
    safe = {
        "schema_version": 1,
        "status": "PASS_READ_ONLY",
        "roots": roots_safe,
        "roots_are_separate_devices": len(existing_devices) == 2 and len(set(existing_devices)) == 2,
        "any_symlinks": any(row["symlink_count"] for row in roots_safe),
        "any_bind_mounts": any(row.get("is_bind_mount") is True for row in roots_safe),
        "quota_command_available": quota_result["returncode"] == 0,
        "quota_command_scope": quota_scope,
        "partial_quota_reallocation": storage_policy["administrative_questions"]["partial_quota_reallocation"],
        "research_tier_backup": storage_policy["administrative_questions"]["research_tier_backup"],
        "research_tier_snapshots": storage_policy["administrative_questions"]["research_tier_snapshots"],
        "research_tier_soft_delete": storage_policy["administrative_questions"]["research_tier_soft_delete"],
        "quota_decimal_or_binary": storage_policy["administrative_questions"]["quota_decimal_or_binary"],
        "files_moved": 0,
        "files_deleted": 0,
    }
    return detail, safe


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-policy", type=Path, required=True)
    parser.add_argument(
        "--safe-export-policy",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "configs"
        / "lvef_multitask_safe_export_policy.yaml",
    )
    parser.add_argument("--restricted-output", type=Path, required=True)
    parser.add_argument("--aggregate-output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        safe_policy, _ = load_safe_export_policy(args.safe_export_policy)
        restricted_output = bind_approved_restricted_path(
            args.restricted_output,
            policy=safe_policy,
            must_exist=False,
            expect="file",
            root_kind="direct",
            create=True,
        )
        aggregate_output = bind_approved_restricted_path(
            args.aggregate_output,
            policy=safe_policy,
            must_exist=False,
            expect="file",
            root_kind="staging",
            create=True,
        )
        policy = yaml.safe_load(args.resource_policy.read_text(encoding="utf-8"))
        detail, safe = audit(policy)
        with restricted_output.open("x", encoding="utf-8") as handle:
            json.dump(detail, handle, indent=2, sort_keys=True)
            handle.write("\n")
        with aggregate_output.open("x", encoding="utf-8") as handle:
            json.dump(safe, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(json.dumps({"status": "FAIL", "error_code": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({"status": safe["status"], "files_moved": 0, "files_deleted": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
