#!/usr/bin/env python3
"""Seal and restore-test the exact Phase 1E-F terminal authority chain.

This tool is offline and fail-closed.  It copies only seven explicitly named,
schema-validated control artifacts to a new owner-private backed-tier root,
restores those bytes into a new isolated research-tier root, and writes one
closed aggregate.  The credential-bearing execution environment is validated
in place but is deliberately not copied.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_lvef_c3_first_batch_command as first_batch
import build_lvef_c3_backup_recovery_witness as primary_backup
import build_lvef_c3_phase1ef_pretransfer_lock as pretransfer
import build_lvef_c3_production_authority_packet as packet
import build_lvef_c3_production_launch_authority as launch
import capture_lvef_c3_production_environment as environment
import finalize_lvef_c3_phase1ef_pretransfer_lock as finalizer


SCHEMA_VERSION = 1
MANIFEST_TYPE = "lvef_c3_terminal_recovery_seal_manifest_v1"
MANIFEST_STATUS = "PASS_TERMINAL_CURRENT_CHAIN_BACKED_SEAL"
RESTORE_TYPE = "lvef_c3_terminal_recovery_restore_receipt_v1"
RESTORE_STATUS = "PASS_TERMINAL_CURRENT_CHAIN_ISOLATED_RESTORE"
AGGREGATE_TYPE = "lvef_c3_terminal_recovery_seal_summary_v1"
AGGREGATE_STATUS = "PASS_TERMINAL_CURRENT_CHAIN_RECOVERABLE_ZERO_SCOPE"
TERMINAL_SEAL_AGGREGATE_FILENAME = (
    "lvef_c3_phase1ef_terminal_recovery_seal.summary.json"
)
BACKED_PREFIX = Path("/restricted/project/mimicecho/audits")
RESEARCH_PREFIX = Path("/restricted/projectnb/mimicecho/audits")
PRIVATE_DIRECTORY_MODES = {0o700, 0o2700}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = pretransfer.UTC_RE
MAXIMUM_ARTIFACT_BYTES = 32_000_000
MAXIMUM_TOTAL_BYTES = 64_000_000

SEALED_ROLES = (
    "current_environment_receipt",
    "pretransfer_composite",
    "backup_recovery_aggregate",
    "production_authority_packet",
    "zero_scope_launch_envelope",
    "future_first_batch_command",
    "final_pretransfer_lock",
)
ROLE_RELATIVE_PATHS = {
    "current_environment_receipt": "files/current_environment_receipt.json",
    "pretransfer_composite": "files/pretransfer_composite.json",
    "backup_recovery_aggregate": "files/backup_recovery_aggregate.json",
    "production_authority_packet": "files/production_authority_packet.json",
    "zero_scope_launch_envelope": "files/zero_scope_launch_envelope.json",
    "future_first_batch_command": "files/future_first_batch_command.sh",
    "final_pretransfer_lock": "files/final_pretransfer_lock.json",
}
ENVIRONMENT_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "governing_commit",
        "captured_at_utc", "source_environment_receipt_sha256",
        "python_executable_sha256", "python_version", "torch_version",
        "torchvision_version", "cuda_version", "cudnn_version",
        "crc32c_runtime_source", "crc32c_python_executable_sha256",
        "crc32c_python_version", "crc32c_worker_sha256",
        "crc32c_worker_protocol_version", "google_crc32c_version",
        "google_crc32c_implementation",
        "google_crc32c_distribution_sha256",
        "google_crc32c_distribution_file_count",
        "google_crc32c_known_vector_base64", "package_inventory_sha256",
        "package_count", "package_inventory", "operating_system",
        "gpu_execution_performed", "cloud_request_performed",
        "dicom_body_read", "model_fitted", "prediction_generated",
        "confirmatory_performance_accessed",
    }
)
MANIFEST_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "production_attempt_id", "governing_commit", "created_at_utc",
        "artifacts", "sealed_role_count", "copied_file_count",
        "copied_bytes", "credential_bearing_execution_environment_excluded",
        "credential_material_absent", "private_project_or_billing_material_absent",
        "unapproved_bulk_scientific_payload_absent", "no_symlinks",
        "no_special_files", "authorization_scopes_granted", "cloud_requests",
        "scheduler_jobs_submitted", "dicom_bodies_downloaded",
        "real_dicom_extraction", "echoprime_inference", "model_fitting",
        "confirmatory_performance_accessed", "full_c3_authorized",
    }
)
RESTORE_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "production_attempt_id", "governing_commit", "created_at_utc",
        "seal_manifest_sha256", "restored_file_count", "restored_bytes",
        "manifest_restore_checksum_equality", "owner_private_permissions_passed",
        "no_symlinks", "no_special_files", "credential_material_absent",
        "private_project_or_billing_material_absent",
        "unapproved_bulk_scientific_payload_absent", "restore_test_passed",
        "authorization_scopes_granted", "cloud_requests",
        "scheduler_jobs_submitted", "dicom_bodies_downloaded",
        "real_dicom_extraction", "echoprime_inference", "model_fitting",
        "confirmatory_performance_accessed", "full_c3_authorized",
    }
)
AGGREGATE_KEYS = frozenset(
    {
        "schema_version", "artifact_type", "status", "attempt_id",
        "production_attempt_id", "governing_commit", "created_at_utc",
        "artifact_bindings", "sealed_role_count", "copied_file_count",
        "copied_bytes", "restored_file_count", "restored_bytes",
        "seal_manifest_size_bytes", "seal_manifest_sha256",
        "restore_receipt_size_bytes", "restore_receipt_sha256",
        "environment_receipt_schema_version", "current_environment_receipt_copied",
        "pretransfer_composite_validated", "authority_packet_roles",
        "authority_packet_semantic_gates", "launch_zero_scope_validated",
        "future_command_validated", "final_lock_validated",
        "cross_hash_bindings_validated", "manifest_restore_checksum_equality",
        "exact_sealed_authority_set_recoverable",
        "credential_bearing_execution_environment_excluded",
        "live_execution_environment_validated_not_copied",
        "owner_private_permissions_passed", "no_symlinks", "no_special_files",
        "credential_material_absent", "private_project_or_billing_material_absent",
        "unapproved_bulk_scientific_payload_absent",
        "authorization_scopes_granted", "cloud_requests",
        "object_listing_repeated", "storage_inventory_repeated",
        "scheduler_jobs_submitted", "dicom_bodies_downloaded",
        "real_dicom_extraction", "echoprime_inference", "model_fitting",
        "confirmatory_performance_accessed", "full_c3_authorized",
    }
)
FORBIDDEN_PATTERNS = tuple(
    re.compile(item)
    for item in (
        r"(?i)\bya29[.][A-Za-z0-9_-]{20,}",
        r"\bAIza[A-Za-z0-9_-]{30,}",
        r"\bGOCSPX-[A-Za-z0-9_-]{20,}",
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----",
        r"(?i)[\"'](?:refresh_token|access_token|id_token|client_secret|private_key|private_key_id)[\"']\s*[:=]\s*[\"'][^\"']+",
        r"(?i)[\"'](?:billing_account|billing_account_id|quota_project|project_id)[\"']\s*:\s*[\"'][^\"']+",
        r"(?i)\bLVEF_C3_GCP_BILLING_PROJECT\s*=",
        r"(?i)gs://",
    )
)


class TerminalRecoverySealError(RuntimeError):
    """Aggregate-safe fail-closed error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _pairs(pairs: Iterable[tuple[str, Any]]) -> MutableMapping[str, Any]:
    value: MutableMapping[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise TerminalRecoverySealError("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _no_symlink_ancestors(path: Path, code: str, *, leaf_required: bool = True) -> None:
    if not path.is_absolute():
        raise TerminalRecoverySealError(f"{code}_NOT_ABSOLUTE")
    cursor = Path(path.anchor)
    parts = path.absolute().parts[1:]
    for index, part in enumerate(parts):
        cursor /= part
        try:
            item = os.lstat(cursor)
        except FileNotFoundError:
            if not leaf_required and index == len(parts) - 1:
                return
            raise TerminalRecoverySealError(f"{code}_ANCESTOR_MISSING") from None
        if stat.S_ISLNK(item.st_mode):
            if sys.platform == "darwin" and cursor == Path("/var"):
                continue
            raise TerminalRecoverySealError(f"{code}_SYMLINK_ANCESTOR")


def _require_private_directory(path: Path, code: str) -> os.stat_result:
    _no_symlink_ancestors(path, code)
    try:
        item = os.lstat(path)
    except OSError as exc:
        raise TerminalRecoverySealError(f"{code}_MISSING") from exc
    if (
        not stat.S_ISDIR(item.st_mode) or stat.S_ISLNK(item.st_mode)
        or item.st_uid != os.getuid()
        or stat.S_IMODE(item.st_mode) not in PRIVATE_DIRECTORY_MODES
    ):
        raise TerminalRecoverySealError(f"{code}_NOT_OWNER_PRIVATE")
    return item


def _create_private_directory(path: Path, *, prefix: Path, code: str) -> None:
    if path.exists() or path.is_symlink():
        raise TerminalRecoverySealError(f"{code}_COLLISION")
    try:
        path.absolute().relative_to(prefix.absolute())
    except ValueError as exc:
        raise TerminalRecoverySealError(f"{code}_OUTSIDE_APPROVED_PREFIX") from exc
    _require_private_directory(path.parent, f"{code}_PARENT")
    os.mkdir(path, 0o700)
    _require_private_directory(path, code)


def _ensure_private_tree(root: Path, relative_parent: PurePosixPath) -> Path:
    current = root
    for part in relative_parent.parts:
        if part in {"", "."}:
            continue
        current /= part
        if current.exists() or current.is_symlink():
            _require_private_directory(current, "DESTINATION_DIRECTORY")
        else:
            os.mkdir(current, 0o700)
            _require_private_directory(current, "DESTINATION_DIRECTORY")
    return current


def _read_private(path: Path, code: str, *, maximum: int = MAXIMUM_ARTIFACT_BYTES) -> bytes:
    _no_symlink_ancestors(path, code)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TerminalRecoverySealError(f"{code}_OPEN_FAILED") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size <= 0
            or before.st_size > maximum
        ):
            raise TerminalRecoverySealError(f"{code}_NOT_BOUNDED_PRIVATE_REGULAR")
        chunks: list[bytes] = []
        size = 0
        while True:
            block = os.read(descriptor, min(1_048_576, maximum + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > maximum:
                raise TerminalRecoverySealError(f"{code}_TOO_LARGE")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or size != after.st_size
    ):
        raise TerminalRecoverySealError(f"{code}_CHANGED_DURING_READ")
    return b"".join(chunks)


def _strict_json(path: Path, code: str) -> tuple[Mapping[str, Any], bytes]:
    payload = _read_private(path, code)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs)
    except TerminalRecoverySealError:
        raise
    except Exception as exc:
        raise TerminalRecoverySealError(f"{code}_INVALID_JSON") from exc
    if not isinstance(value, Mapping):
        raise TerminalRecoverySealError(f"{code}_NOT_MAPPING")
    return value, payload


def _binding_payload(payload: bytes) -> Mapping[str, Any]:
    return {"size_bytes": len(payload), "sha256": _sha(payload)}


def _binding(path: Path, code: str) -> Mapping[str, Any]:
    return _binding_payload(_read_private(path, code))


def _scan_control_payload(payload: bytes, code: str) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TerminalRecoverySealError(f"{code}_NOT_UTF8_CONTROL_ARTIFACT") from exc
    if "\x00" in text or any(pattern.search(text) for pattern in FORBIDDEN_PATTERNS):
        raise TerminalRecoverySealError(f"{code}_FORBIDDEN_PRIVATE_OR_BULK_CONTENT")


def _validate_environment_receipt(value: Mapping[str, Any], governing_commit: str) -> None:
    if set(value) != ENVIRONMENT_KEYS:
        raise TerminalRecoverySealError("ENVIRONMENT_RECEIPT_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != 3
        or value.get("artifact_type") != "lvef_c3_production_environment_authority_v3"
        or value.get("status") != "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION"
        or value.get("governing_commit") != governing_commit
        or not UTC_RE.fullmatch(str(value.get("captured_at_utc", "")))
        or value.get("python_executable_sha256") != environment.EXPECTED_PYTHON_SHA256
        or value.get("crc32c_runtime_source") != "PINNED_CLOUDSDK_BUNDLED_PYTHON"
        or value.get("crc32c_worker_protocol_version") != 1
        or value.get("google_crc32c_implementation") != "c"
        or value.get("google_crc32c_known_vector_base64") != "4waSgw=="
    ):
        raise TerminalRecoverySealError("ENVIRONMENT_RECEIPT_AUTHORITY_INVALID")
    for key in (
        "source_environment_receipt_sha256", "python_executable_sha256",
        "crc32c_python_executable_sha256", "crc32c_worker_sha256",
        "google_crc32c_distribution_sha256", "package_inventory_sha256",
    ):
        if not SHA256_RE.fullmatch(str(value.get(key, ""))):
            raise TerminalRecoverySealError("ENVIRONMENT_RECEIPT_HASH_INVALID")
    packages = value.get("package_inventory")
    if not isinstance(packages, list):
        raise TerminalRecoverySealError("ENVIRONMENT_PACKAGE_INVENTORY_INVALID")
    try:
        validated = environment.validate_package_inventory(packages)
        inventory_sha = environment.package_inventory_sha256(validated)
    except Exception as exc:
        raise TerminalRecoverySealError("ENVIRONMENT_PACKAGE_INVENTORY_INVALID") from exc
    if value.get("package_count") != len(validated) or value.get("package_inventory_sha256") != inventory_sha:
        raise TerminalRecoverySealError("ENVIRONMENT_PACKAGE_INVENTORY_BINDING_MISMATCH")
    for key in (
        "python_version", "torch_version", "torchvision_version", "cuda_version",
        "cudnn_version", "crc32c_python_version", "google_crc32c_version",
        "operating_system",
    ):
        if not isinstance(value.get(key), str) or not value[key]:
            raise TerminalRecoverySealError("ENVIRONMENT_RECEIPT_STRING_INVALID")
    if any(
        value.get(key) is not False
        for key in (
            "gpu_execution_performed", "cloud_request_performed", "dicom_body_read",
            "model_fitted", "prediction_generated", "confirmatory_performance_accessed",
        )
    ):
        raise TerminalRecoverySealError("ENVIRONMENT_RECEIPT_EXECUTION_BOUNDARY_INVALID")


def _validate_future_command(
    *, path: Path, checkout_root: Path, execution_environment: Path,
    launch_envelope: Path, attempt_id: str, governing_commit: str,
) -> None:
    values = packet._parse_execution_environment(execution_environment)
    expected = first_batch.render(
        governing_commit=governing_commit,
        attempt_id=attempt_id,
        dispatcher=checkout_root / "scripts/scc_dispatch_lvef_c3_production_v2.sh",
        execution_environment=execution_environment,
        launch_authority=launch_envelope,
        dispatch_authorization=(
            Path(values["LVEF_C3_DISPATCH_AUTHORIZATION_ROOT"])
            / "FIRST_BATCH_DOWNLOAD.1.dispatch_authorization.json"
        ),
        body_authorization=(
            Path(values["LVEF_C3_DOWNLOAD_AUTHORIZATION_ROOT"])
            / "c3_batch_000.authorization.json"
        ),
    )
    try:
        first_batch.validate_exact(path, expected=expected)
    except first_batch.FirstBatchCommandError as exc:
        raise TerminalRecoverySealError("FUTURE_COMMAND_NOT_EXACT_UNEXECUTED") from exc


def _without_path(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"size_bytes": value.get("size_bytes"), "sha256": value.get("sha256")}


def _validate_chain(
    *, governing_commit: str, checkout_root: Path, current_environment: Path,
    pretransfer_composite: Path, backup_aggregate: Path, authority_packet: Path,
    launch_envelope: Path, future_command: Path, final_lock: Path,
    execution_environment: Path,
) -> tuple[str, Mapping[str, Mapping[str, Any]], Mapping[str, bytes]]:
    try:
        environment.validate_checkout_authority(checkout_root, governing_commit)
    except Exception as exc:
        raise TerminalRecoverySealError("CHECKOUT_AUTHORITY_INVALID") from exc
    env_value, env_payload = _strict_json(current_environment, "CURRENT_ENVIRONMENT")
    pre_value, pre_payload = _strict_json(pretransfer_composite, "PRETRANSFER_COMPOSITE")
    backup_value, backup_payload = _strict_json(
        backup_aggregate, "BACKUP_RECOVERY_AGGREGATE"
    )
    packet_value, packet_payload = _strict_json(authority_packet, "AUTHORITY_PACKET")
    launch_value, launch_payload = _strict_json(launch_envelope, "LAUNCH_ENVELOPE")
    final_value, final_payload = _strict_json(final_lock, "FINAL_LOCK")
    future_payload = _read_private(future_command, "FUTURE_COMMAND")
    execution_binding = _binding(execution_environment, "EXECUTION_ENVIRONMENT")
    for code, payload in (
        ("CURRENT_ENVIRONMENT", env_payload), ("PRETRANSFER_COMPOSITE", pre_payload),
        ("BACKUP_RECOVERY_AGGREGATE", backup_payload),
        ("AUTHORITY_PACKET", packet_payload), ("LAUNCH_ENVELOPE", launch_payload),
        ("FUTURE_COMMAND", future_payload), ("FINAL_LOCK", final_payload),
    ):
        _scan_control_payload(payload, code)
    _validate_environment_receipt(env_value, governing_commit)
    try:
        pretransfer.validate_aggregate_output(pre_value)
        primary_backup.validate_aggregate_output(backup_value)
        packet.validate_packet(packet_value)
        finalizer.validate_aggregate_output(final_value)
    except Exception as exc:
        raise TerminalRecoverySealError("TERMINAL_CHAIN_SCHEMA_OR_SEMANTIC_INVALID") from exc
    production_attempt_id = str(packet_value.get("attempt_id", ""))
    try:
        launch.validate(
            launch_value, envelope_path=launch_envelope,
            attempt_id=production_attempt_id, governing_commit=governing_commit,
            execution_environment=execution_environment, require_terminal_lock=False,
        )
        finalizer.validate_terminal_for_launch(
            final_lock, launch_envelope_path=launch_envelope,
            pretransfer_path=pretransfer_composite, packet_path=authority_packet,
            execution_environment=execution_environment,
            production_attempt_id=production_attempt_id,
            governing_commit=governing_commit,
        )
    except Exception as exc:
        raise TerminalRecoverySealError("TERMINAL_LAUNCH_OR_FINAL_LOCK_INVALID") from exc
    _validate_future_command(
        path=future_command, checkout_root=checkout_root,
        execution_environment=execution_environment,
        launch_envelope=launch_envelope, attempt_id=production_attempt_id,
        governing_commit=governing_commit,
    )
    if (
        pre_value.get("governing_commit") != governing_commit
        or backup_value.get("governing_commit") != governing_commit
        or backup_value.get("attempt_id") != pre_value.get("attempt_id")
        or packet_value.get("governing_commit") != governing_commit
        or launch_value.get("governing_commit") != governing_commit
        or final_value.get("governing_commit") != governing_commit
        or final_value.get("production_attempt_id") != production_attempt_id
        or any(packet_value.get("authorization_scopes", {}).values())
        or len(packet_value.get("authority", {})) != len(packet.REQUIRED_ROLES)
        or len(packet_value.get("semantic_validation", {})) != len(packet.SEMANTIC_VALIDATION_KEYS)
    ):
        raise TerminalRecoverySealError("TERMINAL_CHAIN_IDENTITY_OR_SCOPE_INVALID")
    payloads = {
        "current_environment_receipt": env_payload,
        "pretransfer_composite": pre_payload,
        "backup_recovery_aggregate": backup_payload,
        "production_authority_packet": packet_payload,
        "zero_scope_launch_envelope": launch_payload,
        "future_first_batch_command": future_payload,
        "final_pretransfer_lock": final_payload,
    }
    bindings = {role: _binding_payload(payload) for role, payload in payloads.items()}
    packet_authority = packet_value.get("authority", {})
    final_authority = final_value.get("authority", {})
    launch_expected = {
        "execution_environment": execution_binding,
        "post_expansion_capacity_summary": bindings["pretransfer_composite"],
        "production_authority_packet": bindings["production_authority_packet"],
    }
    if (
        packet_authority.get("environment_receipt") != bindings["current_environment_receipt"]
        or packet_authority.get("post_expansion_capacity_summary") != bindings["pretransfer_composite"]
        or packet_authority.get("future_command_block") != bindings["future_first_batch_command"]
        or pre_value.get("authority", {}).get("backup_recovery_aggregate")
        != bindings["backup_recovery_aggregate"]
        or any(
            _without_path(launch_value.get(role, {})) != binding
            for role, binding in launch_expected.items()
        )
        or final_authority.get("pretransfer_lock") != bindings["pretransfer_composite"]
        or final_authority.get("backup_aggregate")
        != bindings["backup_recovery_aggregate"]
        or final_authority.get("production_authority_packet") != bindings["production_authority_packet"]
        or final_authority.get("launch_envelope") != bindings["zero_scope_launch_envelope"]
        or final_authority.get("future_first_batch_command") != bindings["future_first_batch_command"]
        or final_authority.get("execution_environment") != execution_binding
    ):
        raise TerminalRecoverySealError("TERMINAL_CHAIN_CROSS_HASH_MISMATCH")
    return production_attempt_id, bindings, payloads


def _write_new(path: Path, payload: bytes, code: str) -> None:
    if path.exists() or path.is_symlink():
        raise TerminalRecoverySealError(f"{code}_COLLISION")
    _no_symlink_ancestors(path, code, leaf_required=False)
    _require_private_directory(path.parent, f"{code}_PARENT")
    temporary = path.parent / f".{path.name}.partial.{os.getpid()}.{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    if _read_private(path, code, maximum=max(len(payload), 1)) != payload:
        raise TerminalRecoverySealError(f"{code}_POSTWRITE_MISMATCH")


def _artifact_row(role: str, payload: bytes) -> Mapping[str, Any]:
    return {
        "role": role,
        "backup_relative_path": ROLE_RELATIVE_PATHS[role],
        "size_bytes": len(payload),
        "sha256": _sha(payload),
    }


def _boundary_invalid(value: Mapping[str, Any]) -> bool:
    return (
        value.get("authorization_scopes_granted") != 0
        or value.get("cloud_requests") != 0
        or value.get("scheduler_jobs_submitted") != 0
        or value.get("dicom_bodies_downloaded") != 0
        or value.get("real_dicom_extraction") is not False
        or value.get("echoprime_inference") is not False
        or value.get("model_fitting") is not False
        or value.get("confirmatory_performance_accessed") is not False
        or value.get("full_c3_authorized") is not False
    )


def validate_manifest(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != MANIFEST_KEYS:
        raise TerminalRecoverySealError("TERMINAL_MANIFEST_SCHEMA_NOT_CLOSED")
    artifacts = value.get("artifacts")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != MANIFEST_TYPE
        or value.get("status") != MANIFEST_STATUS
        or not pretransfer.ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not packet.ATTEMPT_RE.fullmatch(str(value.get("production_attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
        or not UTC_RE.fullmatch(str(value.get("created_at_utc", "")))
        or not isinstance(artifacts, list) or len(artifacts) != len(SEALED_ROLES)
        or value.get("sealed_role_count") != len(SEALED_ROLES)
        or value.get("copied_file_count") != len(SEALED_ROLES)
        or _boundary_invalid(value)
    ):
        raise TerminalRecoverySealError("TERMINAL_MANIFEST_AUTHORITY_INVALID")
    expected_row_keys = {"role", "backup_relative_path", "size_bytes", "sha256"}
    if (
        {row.get("role") for row in artifacts if isinstance(row, Mapping)} != set(SEALED_ROLES)
        or any(not isinstance(row, Mapping) or set(row) != expected_row_keys for row in artifacts)
        or any(
            row.get("backup_relative_path") != ROLE_RELATIVE_PATHS[row["role"]]
            or not isinstance(row.get("size_bytes"), int)
            or isinstance(row.get("size_bytes"), bool) or row["size_bytes"] <= 0
            or not SHA256_RE.fullmatch(str(row.get("sha256", "")))
            for row in artifacts
        )
        or value.get("copied_bytes") != sum(row["size_bytes"] for row in artifacts)
    ):
        raise TerminalRecoverySealError("TERMINAL_MANIFEST_ARTIFACTS_INVALID")
    for key in (
        "credential_bearing_execution_environment_excluded", "credential_material_absent",
        "private_project_or_billing_material_absent",
        "unapproved_bulk_scientific_payload_absent", "no_symlinks", "no_special_files",
    ):
        if value.get(key) is not True:
            raise TerminalRecoverySealError("TERMINAL_MANIFEST_SAFETY_GATE_FAILED")


def validate_restore_receipt(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != RESTORE_KEYS:
        raise TerminalRecoverySealError("TERMINAL_RESTORE_SCHEMA_NOT_CLOSED")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != RESTORE_TYPE
        or value.get("status") != RESTORE_STATUS
        or not pretransfer.ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not packet.ATTEMPT_RE.fullmatch(str(value.get("production_attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
        or not UTC_RE.fullmatch(str(value.get("created_at_utc", "")))
        or not SHA256_RE.fullmatch(str(value.get("seal_manifest_sha256", "")))
        or value.get("restored_file_count") != len(SEALED_ROLES)
        or value.get("restored_bytes", 0) <= 0
        or value.get("restore_test_passed") is not True
        or _boundary_invalid(value)
    ):
        raise TerminalRecoverySealError("TERMINAL_RESTORE_AUTHORITY_INVALID")
    for key in (
        "manifest_restore_checksum_equality", "owner_private_permissions_passed",
        "no_symlinks", "no_special_files", "credential_material_absent",
        "private_project_or_billing_material_absent",
        "unapproved_bulk_scientific_payload_absent", "restore_test_passed",
    ):
        if value.get(key) is not True:
            raise TerminalRecoverySealError("TERMINAL_RESTORE_GATE_FAILED")


def validate_aggregate_output(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != AGGREGATE_KEYS:
        raise TerminalRecoverySealError("TERMINAL_AGGREGATE_SCHEMA_NOT_CLOSED")
    bindings = value.get("artifact_bindings")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != AGGREGATE_TYPE
        or value.get("status") != AGGREGATE_STATUS
        or not pretransfer.ATTEMPT_RE.fullmatch(str(value.get("attempt_id", "")))
        or not packet.ATTEMPT_RE.fullmatch(str(value.get("production_attempt_id", "")))
        or not COMMIT_RE.fullmatch(str(value.get("governing_commit", "")))
        or not UTC_RE.fullmatch(str(value.get("created_at_utc", "")))
        or not isinstance(bindings, Mapping) or set(bindings) != set(SEALED_ROLES)
        or value.get("sealed_role_count") != len(SEALED_ROLES)
        or value.get("copied_file_count") != len(SEALED_ROLES)
        or value.get("restored_file_count") != len(SEALED_ROLES)
        or value.get("environment_receipt_schema_version") != 3
        or value.get("authority_packet_roles") != len(packet.REQUIRED_ROLES)
        or value.get("authority_packet_semantic_gates") != len(packet.SEMANTIC_VALIDATION_KEYS)
        or _boundary_invalid(value)
        or value.get("object_listing_repeated") is not False
        or value.get("storage_inventory_repeated") is not False
    ):
        raise TerminalRecoverySealError("TERMINAL_AGGREGATE_AUTHORITY_INVALID")
    for binding in bindings.values():
        if (
            not isinstance(binding, Mapping) or set(binding) != {"size_bytes", "sha256"}
            or not isinstance(binding.get("size_bytes"), int)
            or isinstance(binding.get("size_bytes"), bool) or binding["size_bytes"] <= 0
            or not SHA256_RE.fullmatch(str(binding.get("sha256", "")))
        ):
            raise TerminalRecoverySealError("TERMINAL_AGGREGATE_BINDING_INVALID")
    if (
        value.get("copied_bytes") != sum(item["size_bytes"] for item in bindings.values())
        or value.get("restored_bytes") != value.get("copied_bytes")
    ):
        raise TerminalRecoverySealError("TERMINAL_AGGREGATE_ARITHMETIC_INVALID")
    for key in ("seal_manifest_size_bytes", "restore_receipt_size_bytes"):
        if not isinstance(value.get(key), int) or isinstance(value.get(key), bool) or value[key] <= 0:
            raise TerminalRecoverySealError("TERMINAL_AGGREGATE_SIZE_INVALID")
    for key in ("seal_manifest_sha256", "restore_receipt_sha256"):
        if not SHA256_RE.fullmatch(str(value.get(key, ""))):
            raise TerminalRecoverySealError("TERMINAL_AGGREGATE_HASH_INVALID")
    required_true = set(AGGREGATE_KEYS) - {
        "schema_version", "artifact_type", "status", "attempt_id",
        "production_attempt_id", "governing_commit", "created_at_utc",
        "artifact_bindings", "sealed_role_count", "copied_file_count",
        "copied_bytes", "restored_file_count", "restored_bytes",
        "seal_manifest_size_bytes", "seal_manifest_sha256",
        "restore_receipt_size_bytes", "restore_receipt_sha256",
        "environment_receipt_schema_version", "authority_packet_roles",
        "authority_packet_semantic_gates", "authorization_scopes_granted",
        "cloud_requests", "object_listing_repeated", "storage_inventory_repeated",
        "scheduler_jobs_submitted", "dicom_bodies_downloaded",
        "real_dicom_extraction", "echoprime_inference", "model_fitting",
        "confirmatory_performance_accessed", "full_c3_authorized",
    }
    if any(value.get(key) is not True for key in required_true):
        raise TerminalRecoverySealError("TERMINAL_AGGREGATE_GATE_FAILED")


def _create_manifest(
    *, attempt_id: str, production_attempt_id: str, governing_commit: str,
    payloads: Mapping[str, bytes],
) -> Mapping[str, Any]:
    artifacts = [_artifact_row(role, payloads[role]) for role in SEALED_ROLES]
    value = {
        "schema_version": SCHEMA_VERSION, "artifact_type": MANIFEST_TYPE,
        "status": MANIFEST_STATUS, "attempt_id": attempt_id,
        "production_attempt_id": production_attempt_id,
        "governing_commit": governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts, "sealed_role_count": len(SEALED_ROLES),
        "copied_file_count": len(SEALED_ROLES),
        "copied_bytes": sum(len(payloads[role]) for role in SEALED_ROLES),
        "credential_bearing_execution_environment_excluded": True,
        "credential_material_absent": True,
        "private_project_or_billing_material_absent": True,
        "unapproved_bulk_scientific_payload_absent": True,
        "no_symlinks": True, "no_special_files": True,
        "authorization_scopes_granted": 0, "cloud_requests": 0,
        "scheduler_jobs_submitted": 0, "dicom_bodies_downloaded": 0,
        "real_dicom_extraction": False, "echoprime_inference": False,
        "model_fitting": False, "confirmatory_performance_accessed": False,
        "full_c3_authorized": False,
    }
    validate_manifest(value)
    return value


def _private_tree_files(root: Path, code: str) -> set[str]:
    """Return an exact owner-private regular-file set without following links."""
    _require_private_directory(root, code)
    root_device = os.lstat(root).st_dev
    observed: set[str] = set()
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        metadata = os.lstat(current_path)
        if (
            stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_dev != root_device or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) not in PRIVATE_DIRECTORY_MODES
        ):
            raise TerminalRecoverySealError(f"{code}_TREE_DIRECTORY_INVALID")
        for name in sorted(directories):
            child = current_path / name
            child_metadata = os.lstat(child)
            if stat.S_ISLNK(child_metadata.st_mode) or not stat.S_ISDIR(child_metadata.st_mode):
                raise TerminalRecoverySealError(f"{code}_TREE_SYMLINK_OR_SPECIAL")
        for name in sorted(files):
            child = current_path / name
            child_metadata = os.lstat(child)
            if (
                stat.S_ISLNK(child_metadata.st_mode)
                or not stat.S_ISREG(child_metadata.st_mode)
                or child_metadata.st_uid != os.getuid()
                or stat.S_IMODE(child_metadata.st_mode) != 0o600
            ):
                raise TerminalRecoverySealError(f"{code}_TREE_FILE_INVALID")
            observed.add(child.relative_to(root).as_posix())
    return observed


def _backed_tree_files(root: Path) -> set[str]:
    return _private_tree_files(root, "BACKED_TERMINAL_ROOT")


def validate_backed_terminal_root(
    aggregate_path: Path, *, attempt_id: str, production_attempt_id: str,
    governing_commit: str,
) -> Mapping[str, Any]:
    """Verify the live backed seal, manifest, receipt, and exact file set."""
    seal_root = aggregate_path.parent
    expected_root = BACKED_PREFIX / attempt_id / "terminal_recovery_seal"
    if (
        aggregate_path.name != TERMINAL_SEAL_AGGREGATE_FILENAME
        or seal_root.absolute() != expected_root.absolute()
    ):
        raise TerminalRecoverySealError("BACKED_TERMINAL_AGGREGATE_PATH_INVALID")
    value, _ = _strict_json(aggregate_path, "BACKED_TERMINAL_AGGREGATE")
    manifest_path = seal_root / "terminal_recovery_manifest.restricted.json"
    restore_path = seal_root / "terminal_restore_receipt.restricted.json"
    manifest, manifest_payload = _strict_json(manifest_path, "BACKED_TERMINAL_MANIFEST")
    restore, restore_payload = _strict_json(restore_path, "BACKED_TERMINAL_RESTORE")
    validate_aggregate_output(value)
    validate_manifest(manifest)
    validate_restore_receipt(restore)
    if (
        value.get("attempt_id") != attempt_id
        or value.get("production_attempt_id") != production_attempt_id
        or value.get("governing_commit") != governing_commit
        or manifest.get("attempt_id") != attempt_id
        or manifest.get("production_attempt_id") != production_attempt_id
        or manifest.get("governing_commit") != governing_commit
        or restore.get("attempt_id") != attempt_id
        or restore.get("production_attempt_id") != production_attempt_id
        or restore.get("governing_commit") != governing_commit
        or value.get("seal_manifest_size_bytes") != len(manifest_payload)
        or value.get("seal_manifest_sha256") != _sha(manifest_payload)
        or value.get("restore_receipt_size_bytes") != len(restore_payload)
        or value.get("restore_receipt_sha256") != _sha(restore_payload)
        or restore.get("seal_manifest_sha256") != _sha(manifest_payload)
    ):
        raise TerminalRecoverySealError("BACKED_TERMINAL_RECEIPT_BINDING_MISMATCH")
    manifest_rows = {
        row["role"]: row for row in manifest["artifacts"]
    }
    expected_files = {
        TERMINAL_SEAL_AGGREGATE_FILENAME,
        "terminal_recovery_manifest.restricted.json",
        "terminal_restore_receipt.restricted.json",
        *(ROLE_RELATIVE_PATHS[role] for role in SEALED_ROLES),
    }
    if _backed_tree_files(seal_root) != expected_files:
        raise TerminalRecoverySealError("BACKED_TERMINAL_FILE_SET_MISMATCH")
    for role in SEALED_ROLES:
        payload = _read_private(
            seal_root / ROLE_RELATIVE_PATHS[role], f"BACKED_TERMINAL_{role.upper()}"
        )
        binding = _binding_payload(payload)
        row = manifest_rows.get(role)
        if (
            value["artifact_bindings"].get(role) != binding
            or not isinstance(row, Mapping)
            or row.get("backup_relative_path") != ROLE_RELATIVE_PATHS[role]
            or row.get("size_bytes") != binding["size_bytes"]
            or row.get("sha256") != binding["sha256"]
        ):
            raise TerminalRecoverySealError("BACKED_TERMINAL_ARTIFACT_MISMATCH")
    return value


def execute(args: argparse.Namespace) -> Mapping[str, Any]:
    if not pretransfer.ATTEMPT_RE.fullmatch(args.attempt_id) or not COMMIT_RE.fullmatch(args.governing_commit):
        raise TerminalRecoverySealError("TERMINAL_ARGUMENT_IDENTITY_INVALID")
    if args.seal_root.absolute().is_relative_to(args.restore_root.absolute()) or args.restore_root.absolute().is_relative_to(args.seal_root.absolute()):
        raise TerminalRecoverySealError("TERMINAL_SEAL_AND_RESTORE_OVERLAP")
    if args.aggregate_output.absolute() != (
        args.seal_root / TERMINAL_SEAL_AGGREGATE_FILENAME
    ).absolute():
        raise TerminalRecoverySealError("TERMINAL_AGGREGATE_NOT_BACKED_WITH_SEAL")
    production_attempt_id, bindings, payloads = _validate_chain(
        governing_commit=args.governing_commit, checkout_root=args.checkout_root,
        current_environment=args.current_environment,
        pretransfer_composite=args.pretransfer_composite,
        backup_aggregate=args.backup_aggregate,
        authority_packet=args.authority_packet, launch_envelope=args.launch_envelope,
        future_command=args.future_command, final_lock=args.final_lock,
        execution_environment=args.execution_environment,
    )
    total_bytes = sum(len(payload) for payload in payloads.values())
    if total_bytes > MAXIMUM_TOTAL_BYTES:
        raise TerminalRecoverySealError("TERMINAL_SEALED_SET_TOO_LARGE")
    _create_private_directory(args.seal_root, prefix=BACKED_PREFIX, code="SEAL_ROOT")
    _create_private_directory(args.restore_root, prefix=RESEARCH_PREFIX, code="RESTORE_ROOT")
    for root in (args.seal_root, args.restore_root):
        _ensure_private_tree(root, PurePosixPath("files"))
    for role in SEALED_ROLES:
        relative = Path(ROLE_RELATIVE_PATHS[role])
        _write_new(args.seal_root / relative, payloads[role], f"SEALED_{role.upper()}")
    manifest = _create_manifest(
        attempt_id=args.attempt_id, production_attempt_id=production_attempt_id,
        governing_commit=args.governing_commit, payloads=payloads,
    )
    manifest_payload = _canonical(manifest)
    manifest_path = args.seal_root / "terminal_recovery_manifest.restricted.json"
    _write_new(manifest_path, manifest_payload, "TERMINAL_MANIFEST")
    for role in SEALED_ROLES:
        relative = Path(ROLE_RELATIVE_PATHS[role])
        sealed_payload = _read_private(args.seal_root / relative, f"SEALED_{role.upper()}")
        _scan_control_payload(sealed_payload, f"SEALED_{role.upper()}")
        _write_new(args.restore_root / relative, sealed_payload, f"RESTORED_{role.upper()}")
        restored = _read_private(args.restore_root / relative, f"RESTORED_{role.upper()}")
        _scan_control_payload(restored, f"RESTORED_{role.upper()}")
        if restored != payloads[role]:
            raise TerminalRecoverySealError("TERMINAL_RESTORE_BYTE_MISMATCH")
    if _private_tree_files(args.restore_root, "RESTORED_TERMINAL_ROOT") != {
        ROLE_RELATIVE_PATHS[role] for role in SEALED_ROLES
    }:
        raise TerminalRecoverySealError("TERMINAL_RESTORE_FILE_SET_MISMATCH")
    restore = {
        "schema_version": SCHEMA_VERSION, "artifact_type": RESTORE_TYPE,
        "status": RESTORE_STATUS, "attempt_id": args.attempt_id,
        "production_attempt_id": production_attempt_id,
        "governing_commit": args.governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "seal_manifest_sha256": _sha(manifest_payload),
        "restored_file_count": len(SEALED_ROLES), "restored_bytes": total_bytes,
        "manifest_restore_checksum_equality": True,
        "owner_private_permissions_passed": True, "no_symlinks": True,
        "no_special_files": True, "credential_material_absent": True,
        "private_project_or_billing_material_absent": True,
        "unapproved_bulk_scientific_payload_absent": True,
        "restore_test_passed": True, "authorization_scopes_granted": 0,
        "cloud_requests": 0, "scheduler_jobs_submitted": 0,
        "dicom_bodies_downloaded": 0, "real_dicom_extraction": False,
        "echoprime_inference": False, "model_fitting": False,
        "confirmatory_performance_accessed": False, "full_c3_authorized": False,
    }
    validate_restore_receipt(restore)
    restore_payload = _canonical(restore)
    restore_path = args.seal_root / "terminal_restore_receipt.restricted.json"
    _write_new(restore_path, restore_payload, "TERMINAL_RESTORE_RECEIPT")
    aggregate = {
        "schema_version": SCHEMA_VERSION, "artifact_type": AGGREGATE_TYPE,
        "status": AGGREGATE_STATUS, "attempt_id": args.attempt_id,
        "production_attempt_id": production_attempt_id,
        "governing_commit": args.governing_commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_bindings": dict(sorted(bindings.items())),
        "sealed_role_count": len(SEALED_ROLES), "copied_file_count": len(SEALED_ROLES),
        "copied_bytes": total_bytes, "restored_file_count": len(SEALED_ROLES),
        "restored_bytes": total_bytes,
        "seal_manifest_size_bytes": len(manifest_payload),
        "seal_manifest_sha256": _sha(manifest_payload),
        "restore_receipt_size_bytes": len(restore_payload),
        "restore_receipt_sha256": _sha(restore_payload),
        "environment_receipt_schema_version": 3,
        "current_environment_receipt_copied": True,
        "pretransfer_composite_validated": True,
        "authority_packet_roles": len(packet.REQUIRED_ROLES),
        "authority_packet_semantic_gates": len(packet.SEMANTIC_VALIDATION_KEYS),
        "launch_zero_scope_validated": True, "future_command_validated": True,
        "final_lock_validated": True, "cross_hash_bindings_validated": True,
        "manifest_restore_checksum_equality": True,
        "exact_sealed_authority_set_recoverable": True,
        "credential_bearing_execution_environment_excluded": True,
        "live_execution_environment_validated_not_copied": True,
        "owner_private_permissions_passed": True, "no_symlinks": True,
        "no_special_files": True, "credential_material_absent": True,
        "private_project_or_billing_material_absent": True,
        "unapproved_bulk_scientific_payload_absent": True,
        "authorization_scopes_granted": 0, "cloud_requests": 0,
        "object_listing_repeated": False, "storage_inventory_repeated": False,
        "scheduler_jobs_submitted": 0, "dicom_bodies_downloaded": 0,
        "real_dicom_extraction": False, "echoprime_inference": False,
        "model_fitting": False, "confirmatory_performance_accessed": False,
        "full_c3_authorized": False,
    }
    validate_aggregate_output(aggregate)
    _write_new(args.aggregate_output, _canonical(aggregate), "TERMINAL_AGGREGATE")
    validate_backed_terminal_root(
        args.aggregate_output, attempt_id=args.attempt_id,
        production_attempt_id=production_attempt_id,
        governing_commit=args.governing_commit,
    )
    return aggregate


def validate_terminal_for_launch(
    path: Path, *, launch_envelope_path: Path, final_lock_path: Path,
    pretransfer_path: Path, packet_path: Path, execution_environment: Path,
    production_attempt_id: str, governing_commit: str,
) -> Mapping[str, Any]:
    """Bind a live launch chain to its sibling terminal recovery seal."""
    value = validate_backed_terminal_root(
        path, attempt_id=str(_strict_json(final_lock_path, "TERMINAL_FINAL_LOCK")[0].get("attempt_id", "")),
        production_attempt_id=production_attempt_id,
        governing_commit=governing_commit,
    )
    packet_value, _ = _strict_json(packet_path, "TERMINAL_PACKET")
    final_value, _ = _strict_json(final_lock_path, "TERMINAL_FINAL_LOCK")
    pretransfer_value, _ = _strict_json(pretransfer_path, "TERMINAL_PRETRANSFER")
    if (
        value.get("production_attempt_id") != production_attempt_id
        or value.get("governing_commit") != governing_commit
        or final_value.get("production_attempt_id") != production_attempt_id
        or final_value.get("governing_commit") != governing_commit
    ):
        raise TerminalRecoverySealError("TERMINAL_SEAL_LIVE_IDENTITY_MISMATCH")
    phase1ef_attempt_id = str(final_value.get("attempt_id", ""))
    manifest_binding = final_value.get("authority", {}).get("backup_manifest")
    restore_binding = final_value.get("authority", {}).get("restore_receipt")
    backup_aggregate_binding = final_value.get("authority", {}).get("backup_aggregate")
    if (
        pretransfer_value.get("authority", {}).get("backup_manifest")
        != manifest_binding
        or pretransfer_value.get("authority", {}).get("restore_receipt")
        != restore_binding
        or pretransfer_value.get("authority", {}).get("backup_recovery_aggregate")
        != backup_aggregate_binding
    ):
        raise TerminalRecoverySealError("PRIMARY_BACKUP_CHAIN_BINDING_MISMATCH")
    try:
        primary_backup.validate_live_backup_root(
            BACKED_PREFIX / phase1ef_attempt_id / "control_backup",
            attempt_id=phase1ef_attempt_id, governing_commit=governing_commit,
            expected_manifest_binding=manifest_binding,
            expected_restore_binding=restore_binding,
        )
    except Exception as exc:
        raise TerminalRecoverySealError("PRIMARY_BACKUP_LIVE_VALIDATION_FAILED") from exc
    expected = {
        "pretransfer_composite": _binding(pretransfer_path, "LIVE_PRETRANSFER"),
        "backup_recovery_aggregate": backup_aggregate_binding,
        "production_authority_packet": _binding(packet_path, "LIVE_PACKET"),
        "zero_scope_launch_envelope": _binding(launch_envelope_path, "LIVE_LAUNCH"),
        "final_pretransfer_lock": _binding(final_lock_path, "LIVE_FINAL_LOCK"),
        "current_environment_receipt": packet_value.get("authority", {}).get("environment_receipt"),
        "future_first_batch_command": final_value.get("authority", {}).get("future_first_batch_command"),
    }
    if value.get("artifact_bindings") != expected:
        raise TerminalRecoverySealError("TERMINAL_SEAL_LIVE_BINDING_MISMATCH")
    finalizer.validate_terminal_for_launch(
        final_lock_path, launch_envelope_path=launch_envelope_path,
        pretransfer_path=pretransfer_path, packet_path=packet_path,
        execution_environment=execution_environment,
        production_attempt_id=production_attempt_id,
        governing_commit=governing_commit,
    )
    return value


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--attempt-id", required=True)
    value.add_argument("--governing-commit", required=True)
    value.add_argument("--checkout-root", type=Path, required=True)
    value.add_argument("--current-environment", type=Path, required=True)
    value.add_argument("--pretransfer-composite", type=Path, required=True)
    value.add_argument("--backup-aggregate", type=Path, required=True)
    value.add_argument("--authority-packet", type=Path, required=True)
    value.add_argument("--launch-envelope", type=Path, required=True)
    value.add_argument("--future-command", type=Path, required=True)
    value.add_argument("--final-lock", type=Path, required=True)
    value.add_argument("--execution-environment", type=Path, required=True)
    value.add_argument("--seal-root", type=Path, required=True)
    value.add_argument("--restore-root", type=Path, required=True)
    value.add_argument("--aggregate-output", type=Path, required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    try:
        aggregate = execute(parser().parse_args(argv))
    except (TerminalRecoverySealError, OSError, ValueError, KeyError) as exc:
        code = exc.code if isinstance(exc, TerminalRecoverySealError) else "TERMINAL_RECOVERY_SEAL_FAILED"
        print(json.dumps({"status": "FAIL", "error_code": code}, sort_keys=True))
        return 78
    print(json.dumps({
        "status": aggregate["status"], "sealed_role_count": len(SEALED_ROLES),
        "authorization_scopes_granted": 0, "cloud_requests": 0,
        "scheduler_jobs_submitted": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
