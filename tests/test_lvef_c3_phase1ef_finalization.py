from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import finalize_lvef_c3_phase1ef_pretransfer_lock as final
import build_lvef_c3_phase1ef_pretransfer_lock as pretransfer
import lvef_multitask_analysis_modes as analysis_modes


def _value() -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_type": final.ARTIFACT_TYPE,
        "status": final.STATUS,
        "attempt_id": "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001",
        "production_attempt_id": "lvef_c3_phase1ee_production_lock_006",
        "governing_commit": "a" * 40,
        "created_at_utc": "2026-08-11T12:00:00+00:00",
        "authority": {role: {"size_bytes": 1, "sha256": "b" * 64} for role in final.AUTHORITY_ROLES},
        "capacity": {
            "research_quota_bytes": 100,
            "research_usage_bytes": 10,
            "research_quota_remaining_bytes": 90,
            "research_file_quota": 100,
            "research_files_used": 10,
            "research_file_slots_remaining": 90,
            "research_filesystem_available_bytes": 1_000,
            "backed_quota_bytes": 50,
            "backed_usage_bytes": 5,
            "backed_quota_remaining_bytes": 45,
            "backed_file_quota": 50,
            "backed_files_used": 5,
            "backed_file_slots_remaining": 45,
            "research_quota_gate_passed": True,
            "physical_filesystem_capacity_gate_passed": True,
            "projected_200gb_reserve_gate_passed": True,
            "research_file_quota_gate_passed": True,
            "backed_control_tier_gate_passed": True,
            "research_quota_margin_above_minimum_bytes": 1,
            "research_quota_slack_after_projected_peak_bytes": 1,
            "research_margin_beyond_200gb_reserve_bytes": 1,
            "research_physical_slack_bytes": 1,
        },
        "backup_recovery": {
            "classification_counts": {
                "GIT_ORIGIN_PROTECTED": 1,
                "COMMITTED_RECONSTRUCTABLE": 1,
                "PINNED_EXTERNAL_SOURCE_RECONSTRUCTABLE": 1,
                "OWNER_RECREATABLE": 1,
                "CHECKSUM_ONLY_NO_COPY_REQUIRED": 1,
                "IRREPLACEABLE_BACKUP_REQUIRED": 1,
                "EXCLUDED_CREDENTIAL_MATERIAL": 1,
                "UNRESOLVED": 0,
            },
            "declared_item_count": 7,
            "copied_file_count": 1,
            "copied_bytes": 10,
            "git_bundle_bytes": 10,
            "restored_file_count": 1,
            "restored_bytes": 10,
            "backup_verified": True,
            "restore_test_passed": True,
            "credential_material_absent": True,
            "private_project_or_billing_material_absent": True,
            "unapproved_bulk_scientific_payload_absent": True,
            "unresolved_item_count": 0,
        },
        "production_lock": {
            "authority_roles": 38,
            "semantic_gates": 17,
            "authorization_scope_count": 7,
            "authorization_scopes_granted": 0,
            "launch_envelope_created": True,
            "owner_stage_authorization_granted": False,
            "scheduler_submission_performed": False,
        },
        "execution_attestations": dict(pretransfer.EXECUTION_ATTESTATIONS),
        "authorization_scopes_granted": 0,
        "full_c3_status": "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION",
    }


def test_final_lock_is_closed_38_17_and_zero_scope() -> None:
    value = _value()
    final.validate_aggregate_output(value)
    assert value["production_lock"]["authority_roles"] == 38
    assert value["production_lock"]["semantic_gates"] == 17
    assert value["authorization_scopes_granted"] == 0
    policy, _ = analysis_modes.load_policy(
        ROOT / "configs/lvef_multitask_safe_export_policy.yaml"
    )
    safe = analysis_modes.validate_candidate_bytes(
        (json.dumps(value, sort_keys=True) + "\n").encode(),
        filename="lvef_c3_phase1ef_final_pretransfer_lock.summary.json",
        profile_name="phase1ef_final_pretransfer_lock_json",
        policy=policy,
    )
    assert safe["status"] == "PASS"


def test_final_lock_rejects_unknown_key_and_every_execution() -> None:
    value = _value(); value["unexpected"] = False
    try:
        final.validate_aggregate_output(value)
    except final.Phase1EFFinalizationError as exc:
        assert str(exc) == "FINAL_LOCK_SCHEMA_NOT_CLOSED"
    else:
        raise AssertionError("unknown final-lock key accepted")
    value = _value(); value["execution_attestations"]["cloud_requests"] = 1
    try:
        final.validate_aggregate_output(value)
    except final.Phase1EFFinalizationError as exc:
        assert str(exc) == "FINAL_LOCK_EXECUTION_BOUNDARY_INVALID"
    else:
        raise AssertionError("executed cloud request accepted")


def test_final_lock_rejects_failed_capacity_backup_or_packet_gate() -> None:
    for group, key in (
        ("capacity", "backed_control_tier_gate_passed"),
        ("backup_recovery", "restore_test_passed"),
        ("production_lock", "launch_envelope_created"),
    ):
        value = _value(); value[group][key] = False
        try:
            final.validate_aggregate_output(value)
        except final.Phase1EFFinalizationError as exc:
            assert str(exc) == "FINAL_LOCK_GATE_NOT_PASS"
        else:
            raise AssertionError(f"failed {group}.{key} accepted")


def test_final_lock_rejects_nested_schema_and_scheduler_claim() -> None:
    value = _value()
    value["capacity"]["unexpected"] = 0
    try:
        final.validate_aggregate_output(value)
    except final.Phase1EFFinalizationError as exc:
        assert str(exc) == "FINAL_LOCK_CAPACITY_SCHEMA_NOT_CLOSED"
    else:
        raise AssertionError("open nested capacity schema was accepted")
    value = _value()
    value["production_lock"]["scheduler_submission_performed"] = True
    try:
        final.validate_aggregate_output(value)
    except final.Phase1EFFinalizationError as exc:
        assert str(exc) == "FINAL_LOCK_GATE_NOT_PASS"
    else:
        raise AssertionError("scheduler execution was accepted")


def test_terminal_final_lock_binds_live_launch_packet_composite_and_environment() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        paths = {}
        for role in ("launch_envelope", "pretransfer_lock", "production_authority_packet", "execution_environment"):
            path = root / f"{role}.json"
            path.write_text(f"{{\"role\":\"{role}\"}}\n", encoding="utf-8")
            path.chmod(0o600)
            paths[role] = path
        value = _value()
        for role, path in paths.items():
            value["authority"][role] = final._binding(path)
        terminal = root / "lvef_c3_phase1ef_final_pretransfer_lock.summary.json"
        terminal.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        terminal.chmod(0o600)
        final.validate_terminal_for_launch(
            terminal,
            launch_envelope_path=paths["launch_envelope"],
            pretransfer_path=paths["pretransfer_lock"],
            packet_path=paths["production_authority_packet"],
            execution_environment=paths["execution_environment"],
            production_attempt_id="lvef_c3_phase1ee_production_lock_006",
            governing_commit="a" * 40,
        )
        paths["production_authority_packet"].write_text(
            "{\"role\":\"tampered\"}\n", encoding="utf-8"
        )
        try:
            final.validate_terminal_for_launch(
                terminal,
                launch_envelope_path=paths["launch_envelope"],
                pretransfer_path=paths["pretransfer_lock"],
                packet_path=paths["production_authority_packet"],
                execution_environment=paths["execution_environment"],
                production_attempt_id="lvef_c3_phase1ee_production_lock_006",
                governing_commit="a" * 40,
            )
        except final.Phase1EFFinalizationError as exc:
            assert str(exc) == "TERMINAL_LOCK_CHAIN_BINDING_MISMATCH"
        else:
            raise AssertionError("terminal lock accepted a tampered packet")


def test_finalizer_rejects_mix_and_match_composite_packet_and_command_chain() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        paths = {}
        for role in final.AUTHORITY_ROLES:
            path = root / f"{role}.json"
            path.write_text(f"{{\"role\":\"{role}\"}}\n", encoding="utf-8")
            path.chmod(0o600)
            paths[role] = path
        bindings = {role: final._binding(path) for role, path in paths.items()}
        capacity = {
            "restricted_receipt_size_bytes": bindings["capacity_receipt"]["size_bytes"],
            "restricted_receipt_sha256": bindings["capacity_receipt"]["sha256"],
        }
        backup = {
            "backup_manifest_sha256": bindings["backup_manifest"]["sha256"],
            "restore_receipt_sha256": bindings["restore_receipt"]["sha256"],
        }
        composite = {
            "authority": {
                "post_reallocation_capacity_receipt": bindings["capacity_receipt"],
                "post_reallocation_capacity_aggregate": bindings["capacity_aggregate"],
                "backup_manifest": bindings["backup_manifest"],
                "backup_recovery_aggregate": bindings["backup_aggregate"],
                "restore_test_receipt": bindings["restore_receipt"],
                "future_first_batch_command": bindings["future_first_batch_command"],
            }
        }
        packet_value = {
            "authority": {
                "post_expansion_capacity_summary": bindings["pretransfer_lock"],
                "future_command_block": bindings["future_first_batch_command"],
                "execution_environment": bindings["execution_environment"],
            }
        }
        launch_value = {}
        for launch_role, binding_role in {
            "execution_environment": "execution_environment",
            "post_expansion_capacity_summary": "pretransfer_lock",
            "production_authority_packet": "production_authority_packet",
        }.items():
            launch_value[launch_role] = {
                "path": str(paths[binding_role].resolve()),
                **bindings[binding_role],
            }
        final._validate_cross_bindings(
            bindings=bindings, capacity_value=capacity, backup_value=backup,
            pretransfer_value=composite, packet_value=packet_value,
            launch_value=launch_value, paths=paths,
        )
        packet_value["authority"]["future_command_block"] = {
            "size_bytes": 1, "sha256": "f" * 64
        }
        try:
            final._validate_cross_bindings(
                bindings=bindings, capacity_value=capacity, backup_value=backup,
                pretransfer_value=composite, packet_value=packet_value,
                launch_value=launch_value, paths=paths,
            )
        except final.Phase1EFFinalizationError as exc:
            assert str(exc) == "FINAL_LOCK_PACKET_BINDING_MISMATCH"
        else:
            raise AssertionError("finalizer accepted a mixed command/packet chain")


def test_packet_and_pretransfer_attestation_vocabularies_are_explicit() -> None:
    assert "storage_audit_repeated" in final.packet.EXECUTION_ATTESTATIONS
    assert "storage_inventory_repeated" in pretransfer.EXECUTION_ATTESTATIONS
    assert final.packet.EXECUTION_ATTESTATIONS != pretransfer.EXECUTION_ATTESTATIONS
