from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: no test contacts a network or reads SCC data.

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_lvef_c3_terminal_recovery_seal as seal
import lvef_multitask_analysis_modes as analysis_modes


ATTEMPT = "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001"
PRODUCTION_ATTEMPT = "lvef_c3_phase1ee_production_lock_006"
COMMIT = "a" * 40


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True)
    path.chmod(0o700)
    return path


def _private_file(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _payloads() -> dict[str, bytes]:
    return {
        role: (
            b"#!/usr/bin/env bash\n# UNEXECUTED SYNTHETIC COMMAND\n"
            if role == "future_first_batch_command"
            else (json.dumps({"synthetic_role": role}, sort_keys=True) + "\n").encode()
        )
        for role in seal.SEALED_ROLES
    }


def _bindings(payloads: dict[str, bytes]) -> dict[str, dict[str, object]]:
    return {
        role: dict(seal._binding_payload(payload))
        for role, payload in payloads.items()
    }


def _aggregate(payloads: dict[str, bytes] | None = None) -> dict[str, object]:
    payloads = payloads or _payloads()
    bindings = _bindings(payloads)
    copied = sum(item["size_bytes"] for item in bindings.values())
    return {
        "schema_version": 1,
        "artifact_type": seal.AGGREGATE_TYPE,
        "status": seal.AGGREGATE_STATUS,
        "attempt_id": ATTEMPT,
        "production_attempt_id": PRODUCTION_ATTEMPT,
        "governing_commit": COMMIT,
        "created_at_utc": "2026-08-11T12:00:00+00:00",
        "artifact_bindings": bindings,
        "sealed_role_count": len(seal.SEALED_ROLES),
        "copied_file_count": len(seal.SEALED_ROLES),
        "copied_bytes": copied,
        "restored_file_count": len(seal.SEALED_ROLES),
        "restored_bytes": copied,
        "seal_manifest_size_bytes": 100,
        "seal_manifest_sha256": "b" * 64,
        "restore_receipt_size_bytes": 100,
        "restore_receipt_sha256": "c" * 64,
        "environment_receipt_schema_version": 3,
        "current_environment_receipt_copied": True,
        "pretransfer_composite_validated": True,
        "authority_packet_roles": 38,
        "authority_packet_semantic_gates": 17,
        "launch_zero_scope_validated": True,
        "future_command_validated": True,
        "final_lock_validated": True,
        "cross_hash_bindings_validated": True,
        "manifest_restore_checksum_equality": True,
        "exact_sealed_authority_set_recoverable": True,
        "credential_bearing_execution_environment_excluded": True,
        "live_execution_environment_validated_not_copied": True,
        "owner_private_permissions_passed": True,
        "no_symlinks": True,
        "no_special_files": True,
        "credential_material_absent": True,
        "private_project_or_billing_material_absent": True,
        "unapproved_bulk_scientific_payload_absent": True,
        "authorization_scopes_granted": 0,
        "cloud_requests": 0,
        "object_listing_repeated": False,
        "storage_inventory_repeated": False,
        "scheduler_jobs_submitted": 0,
        "dicom_bodies_downloaded": 0,
        "real_dicom_extraction": False,
        "echoprime_inference": False,
        "model_fitting": False,
        "confirmatory_performance_accessed": False,
        "full_c3_authorized": False,
    }


def _expect(code: str, function) -> None:
    try:
        function()
    except seal.TerminalRecoverySealError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"Expected TerminalRecoverySealError({code})")


def test_aggregate_is_closed_zero_scope_and_safe_exportable() -> None:
    assert seal.TERMINAL_SEAL_AGGREGATE_FILENAME == (
        "lvef_c3_phase1ef_terminal_recovery_seal.summary.json"
    )
    value = _aggregate()
    seal.validate_aggregate_output(value)
    policy, _ = analysis_modes.load_policy(
        ROOT / "configs/lvef_multitask_safe_export_policy.yaml"
    )
    result = analysis_modes.validate_candidate_bytes(
        (json.dumps(value, sort_keys=True) + "\n").encode(),
        filename=seal.TERMINAL_SEAL_AGGREGATE_FILENAME,
        profile_name="phase1ef_terminal_recovery_seal_json",
        policy=policy,
    )
    assert result["status"] == "PASS"


def test_aggregate_rejects_open_schema_and_execution() -> None:
    value = _aggregate()
    value["unexpected"] = False
    _expect("TERMINAL_AGGREGATE_SCHEMA_NOT_CLOSED", lambda: seal.validate_aggregate_output(value))
    value = _aggregate()
    value["cloud_requests"] = 1
    _expect("TERMINAL_AGGREGATE_AUTHORITY_INVALID", lambda: seal.validate_aggregate_output(value))


def test_forbidden_credentials_private_project_and_bulk_locator_are_rejected() -> None:
    for payload in (
        b'{"access_token":"synthetic-secret-value"}\n',
        b'{"project_id":"private-project"}\n',
        b'{"source":"gs://synthetic-bucket/object"}\n',
    ):
        _expect(
            "SEALED_FORBIDDEN_PRIVATE_OR_BULK_CONTENT",
            lambda payload=payload: seal._scan_control_payload(payload, "SEALED"),
        )


def test_execute_copies_exact_seven_role_chain_and_restores_without_execution_env() -> None:
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory).resolve()
        backed = _private_directory(tmp_path / "backed")
        research = _private_directory(tmp_path / "research")
        backed_attempt = _private_directory(backed / ATTEMPT)
        inputs = _private_directory(tmp_path / "inputs")
        execution_environment = _private_file(
            inputs / "execution.restricted.env",
            b"LVEF_C3_GCP_BILLING_PROJECT='synthetic-private-project'\n",
        )
        payloads = _payloads()
        bindings = _bindings(payloads)
        args = argparse.Namespace(
            attempt_id=ATTEMPT,
            governing_commit=COMMIT,
            checkout_root=tmp_path,
            current_environment=inputs / "environment.json",
            pretransfer_composite=inputs / "pretransfer.json",
            backup_aggregate=inputs / "backup_aggregate.json",
            authority_packet=inputs / "packet.json",
            launch_envelope=inputs / "launch.json",
            future_command=inputs / "future.sh",
            final_lock=inputs / "final.json",
            execution_environment=execution_environment,
            seal_root=backed_attempt / "terminal_recovery_seal",
            restore_root=research / "terminal_restore",
            aggregate_output=(
                backed_attempt / "terminal_recovery_seal"
                / seal.TERMINAL_SEAL_AGGREGATE_FILENAME
            ),
        )
        with mock.patch.object(seal, "BACKED_PREFIX", backed), mock.patch.object(
            seal, "RESEARCH_PREFIX", research
        ), mock.patch.object(
            seal, "_validate_chain", return_value=(PRODUCTION_ATTEMPT, bindings, payloads)
        ):
            aggregate = seal.execute(args)
        seal.validate_aggregate_output(aggregate)
        for role in seal.SEALED_ROLES:
            relative = Path(seal.ROLE_RELATIVE_PATHS[role])
            assert (args.seal_root / relative).read_bytes() == payloads[role]
            assert (args.restore_root / relative).read_bytes() == payloads[role]
        assert not any(
            path.read_bytes() == execution_environment.read_bytes()
            for path in args.seal_root.rglob("*") if path.is_file()
        )
        assert aggregate["cloud_requests"] == 0
        assert aggregate["scheduler_jobs_submitted"] == 0
        first_role = seal.SEALED_ROLES[0]
        (args.seal_root / seal.ROLE_RELATIVE_PATHS[first_role]).write_bytes(b"tampered")
        (args.seal_root / seal.ROLE_RELATIVE_PATHS[first_role]).chmod(0o600)
        with mock.patch.object(seal, "BACKED_PREFIX", backed):
            _expect(
                "BACKED_TERMINAL_ARTIFACT_MISMATCH",
                lambda: seal.validate_backed_terminal_root(
                    args.aggregate_output, attempt_id=ATTEMPT,
                    production_attempt_id=PRODUCTION_ATTEMPT,
                    governing_commit=COMMIT,
                ),
            )
        with mock.patch.object(seal, "BACKED_PREFIX", backed), mock.patch.object(
            seal, "RESEARCH_PREFIX", research
        ), mock.patch.object(
            seal, "_validate_chain", return_value=(PRODUCTION_ATTEMPT, bindings, payloads)
        ):
            _expect("SEAL_ROOT_COLLISION", lambda: seal.execute(args))


def test_symlink_source_and_setgid_private_destination_handling() -> None:
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory).resolve()
        private = _private_directory(tmp_path / "private")
        target = _private_file(private / "target.json", b"{}\n")
        link = private / "link.json"
        link.symlink_to(target)
        _expect("SOURCE_SYMLINK_ANCESTOR", lambda: seal._read_private(link, "SOURCE"))
        backed = _private_directory(tmp_path / "backed")
        backed.chmod(0o2700)
        created = backed / "new"
        with mock.patch.object(seal, "BACKED_PREFIX", backed):
            seal._create_private_directory(created, prefix=backed, code="DESTINATION")
        assert created.stat().st_mode & 0o7777 in seal.PRIVATE_DIRECTORY_MODES


def test_validate_terminal_for_launch_binds_live_chain() -> None:
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory).resolve()
        tmp_path.chmod(0o700)
        backup_binding = {"size_bytes": 13, "sha256": "f" * 64}
        restore_binding = {"size_bytes": 14, "sha256": "1" * 64}
        backup_aggregate_binding = {"size_bytes": 15, "sha256": "2" * 64}
        pre = _private_file(
            tmp_path / "pre.json",
            (json.dumps({
                "authority": {
                    "backup_manifest": backup_binding,
                    "backup_recovery_aggregate": backup_aggregate_binding,
                    "restore_receipt": restore_binding,
                }
            }, sort_keys=True) + "\n").encode(),
        )
        launch = _private_file(tmp_path / "launch.json", b'{"synthetic":"launch"}\n')
        env = _private_file(tmp_path / "execution.env", b"SYNTHETIC=1\n")
        current_environment_binding = {"size_bytes": 11, "sha256": "d" * 64}
        future_binding = {"size_bytes": 12, "sha256": "e" * 64}
        packet_value = {
            "authority": {"environment_receipt": current_environment_binding}
        }
        packet_path = _private_file(
            tmp_path / "packet.json",
            (json.dumps(packet_value, sort_keys=True) + "\n").encode(),
        )
        final_value = {
            "attempt_id": ATTEMPT,
            "production_attempt_id": PRODUCTION_ATTEMPT,
            "governing_commit": COMMIT,
            "authority": {
                "future_first_batch_command": future_binding,
                "backup_manifest": backup_binding,
                "backup_aggregate": backup_aggregate_binding,
                "restore_receipt": restore_binding,
            },
        }
        final_path = _private_file(
            tmp_path / "final.json",
            (json.dumps(final_value, sort_keys=True) + "\n").encode(),
        )
        value = _aggregate()
        value["artifact_bindings"] = {
            "current_environment_receipt": current_environment_binding,
            "pretransfer_composite": dict(seal._binding(pre, "PRE")),
            "backup_recovery_aggregate": backup_aggregate_binding,
            "production_authority_packet": dict(seal._binding(packet_path, "PACKET")),
            "zero_scope_launch_envelope": dict(seal._binding(launch, "LAUNCH")),
            "future_first_batch_command": future_binding,
            "final_pretransfer_lock": dict(seal._binding(final_path, "FINAL")),
        }
        value["copied_bytes"] = sum(
            item["size_bytes"] for item in value["artifact_bindings"].values()
        )
        value["restored_bytes"] = value["copied_bytes"]
        aggregate_path = _private_file(
            tmp_path / seal.TERMINAL_SEAL_AGGREGATE_FILENAME,
            (json.dumps(value, sort_keys=True) + "\n").encode(),
        )
        with mock.patch.object(
            seal, "validate_backed_terminal_root", return_value=value
        ), mock.patch.object(
            seal.primary_backup, "validate_live_backup_root", return_value={}
        ), mock.patch.object(seal.finalizer, "validate_terminal_for_launch"):
            observed = seal.validate_terminal_for_launch(
                aggregate_path,
                launch_envelope_path=launch,
                final_lock_path=final_path,
                pretransfer_path=pre,
                packet_path=packet_path,
                execution_environment=env,
                production_attempt_id=PRODUCTION_ATTEMPT,
                governing_commit=COMMIT,
            )
        assert observed["status"] == seal.AGGREGATE_STATUS
        pre.write_bytes(
            (json.dumps({
                "authority": {
                    "backup_manifest": backup_binding,
                    "backup_recovery_aggregate": backup_aggregate_binding,
                    "restore_receipt": restore_binding,
                },
                "synthetic_tamper": True,
            }, sort_keys=True) + "\n").encode()
        )
        pre.chmod(0o600)
        with mock.patch.object(
            seal, "validate_backed_terminal_root", return_value=value
        ), mock.patch.object(
            seal.primary_backup, "validate_live_backup_root", return_value={}
        ), mock.patch.object(seal.finalizer, "validate_terminal_for_launch"):
            _expect(
                "TERMINAL_SEAL_LIVE_BINDING_MISMATCH",
                lambda: seal.validate_terminal_for_launch(
                    aggregate_path,
                    launch_envelope_path=launch,
                    final_lock_path=final_path,
                    pretransfer_path=pre,
                    packet_path=packet_path,
                    execution_environment=env,
                    production_attempt_id=PRODUCTION_ATTEMPT,
                    governing_commit=COMMIT,
                ),
            )
