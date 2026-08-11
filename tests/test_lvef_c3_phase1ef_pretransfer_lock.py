from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_lvef_c3_phase1ef_pretransfer_lock as lock
import lvef_multitask_analysis_modes as analysis_modes


def _aggregate() -> dict[str, object]:
    return {
        "schema_version": lock.SCHEMA_VERSION,
        "artifact_type": lock.ARTIFACT_TYPE,
        "status": lock.STATUS,
        "attempt_id": "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001",
        "governing_commit": "a" * 40,
        "created_at_utc": "2026-08-11T12:00:00Z",
        "authority": {
            role: {"size_bytes": 10, "sha256": "b" * 64}
            for role in lock.AUTHORITY_ROLES
        },
        "capacity_gates": {key: True for key in lock.CAPACITY_GATE_KEYS},
        "backup_recovery_gates": {
            key: True for key in lock.BACKUP_GATE_KEYS
        },
        "write_binding_gates": {key: True for key in lock.WRITE_BINDING_KEYS},
        "execution_attestations": dict(lock.EXECUTION_ATTESTATIONS),
        "authorization_scopes_granted": 0,
        "full_c3_status": "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION",
    }


def _backup() -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_type": lock.BACKUP_ARTIFACT_TYPE,
        "status": lock.BACKUP_STATUS,
        "attempt_id": "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001",
        "governing_commit": "a" * 40,
        "policy_sha256": "1" * 64,
        "selection_sha256": "2" * 64,
        "recovery_documentation_sha256": "6" * 64,
        "backup_manifest_sha256": "3" * 64,
        "restore_receipt_sha256": "4" * 64,
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
        "copied_file_count": 1,
        "copied_bytes": 10,
        "restored_file_count": 1,
        "restored_bytes": 10,
        "git_bundle_sha256": "5" * 64,
        "tracked_file_set_sha256": "7" * 64,
        "git_bundle_bytes": 10,
        **{key: True for key in lock.BACKUP_GATE_KEYS},
        "cloud_requests": 0,
        "scheduler_jobs_submitted": 0,
        "object_listing_repeated": False,
        "dicom_bodies_downloaded": 0,
        "full_c3_authorized": False,
    }


def test_phase1ef_composite_is_closed_launch_ready_and_zero_scope() -> None:
    value = _aggregate()
    lock.validate_aggregate_output(value)
    assert lock.launch_ready(value) is True
    assert value["authorization_scopes_granted"] == 0
    assert value["execution_attestations"]["cloud_requests"] == 0
    assert value["execution_attestations"]["scheduler_jobs_submitted"] == 0
    policy, _ = analysis_modes.load_policy(
        ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"
    )
    result = analysis_modes.validate_candidate_bytes(
        (json.dumps(value, sort_keys=True) + "\n").encode("utf-8"),
        filename="lvef_c3_phase1ef_pretransfer_lock.summary.json",
        profile_name="phase1ef_pretransfer_lock_json",
        policy=policy,
    )
    assert result["status"] == "PASS"


def test_phase1ef_composite_rejects_unknown_key_and_every_failed_gate() -> None:
    value = _aggregate()
    value["unexpected"] = False
    try:
        lock.validate_aggregate_output(value)
    except lock.Phase1EFPretransferError as exc:
        assert str(exc) == "PRETRANSFER_SCHEMA_NOT_CLOSED"
    else:
        raise AssertionError("unknown aggregate key was accepted")
    for group in (
        "capacity_gates",
        "backup_recovery_gates",
        "write_binding_gates",
    ):
        changed = _aggregate()
        first = next(iter(changed[group]))
        changed[group][first] = False
        assert lock.launch_ready(changed) is False


def test_capacity_receipt_must_be_hash_bound_and_closed_validated() -> None:
    attempt = "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001"
    receipt = {"attempt_id": attempt, "governing_commit": "a" * 40}
    capacity = {
        "attempt_id": attempt,
        "governing_commit": "a" * 40,
        "restricted_receipt_size_bytes": 17,
        "restricted_receipt_sha256": "a" * 64,
        **{key: True for key in lock.CAPACITY_GATE_KEYS},
    }
    validator = SimpleNamespace(
        validate_receipt_output=lambda value: None,
        validate_aggregate_output=lambda value: None,
    )
    with mock.patch.object(lock.importlib, "import_module", return_value=validator):
        lock._validate_capacity(
            capacity, receipt=receipt,
            receipt_binding={"size_bytes": 17, "sha256": "a" * 64},
            attempt_id=attempt, governing_commit="a" * 40,
        )
        try:
            lock._validate_capacity(
                capacity, receipt=receipt,
                receipt_binding={"size_bytes": 17, "sha256": "b" * 64},
                attempt_id=attempt, governing_commit="a" * 40,
            )
        except lock.Phase1EFPretransferError as exc:
            assert str(exc) == "CAPACITY_RECEIPT_BINDING_MISMATCH"
        else:
            raise AssertionError("unbound capacity receipt was accepted")
        changed_receipt = dict(receipt)
        changed_receipt["attempt_id"] = (
            "lvef_multitask_phase1ef_post_reallocation_lock_attempt_002"
        )
        try:
            lock._validate_capacity(
                capacity, receipt=changed_receipt,
                receipt_binding={"size_bytes": 17, "sha256": "a" * 64},
                attempt_id=attempt, governing_commit="a" * 40,
            )
        except lock.Phase1EFPretransferError as exc:
            assert str(exc) == "CAPACITY_AUTHORITY_IDENTITY_MISMATCH"
        else:
            raise AssertionError("mixed-attempt capacity receipt was accepted")


def test_backup_manifest_restore_and_credentials_are_fail_closed() -> None:
    attempt = "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001"
    value = _backup()
    manifest = {
        "attempt_id": attempt, "governing_commit": "a" * 40,
        "policy_sha256": "1" * 64, "selection_sha256": "2" * 64,
        "recovery_documentation_sha256": value["recovery_documentation_sha256"],
    }
    restore = {
        "attempt_id": attempt, "governing_commit": "a" * 40,
        "policy_sha256": "1" * 64, "backup_manifest_sha256": "3" * 64,
        "git_bundle_sha256": "5" * 64,
        "tracked_file_set_sha256": value.get("tracked_file_set_sha256"),
    }
    validator = SimpleNamespace(
        validate_backup_manifest=lambda item: None,
        validate_restore_receipt=lambda item: None,
        validate_aggregate_output=lambda item: None,
    )
    with mock.patch.object(lock.importlib, "import_module", return_value=validator):
        lock._validate_backup(
            value, manifest=manifest, restore=restore, attempt_id=attempt,
            governing_commit="a" * 40,
            manifest_binding={"size_bytes": 1, "sha256": "3" * 64},
            restore_binding={"size_bytes": 1, "sha256": "4" * 64},
        )
        changed = copy.deepcopy(value)
        changed["credential_material_absent"] = False
        try:
            lock._validate_backup(
                changed, manifest=manifest, restore=restore, attempt_id=attempt,
                governing_commit="a" * 40,
                manifest_binding={"size_bytes": 1, "sha256": "3" * 64},
                restore_binding={"size_bytes": 1, "sha256": "4" * 64},
            )
        except lock.Phase1EFPretransferError as exc:
            assert str(exc) == "BACKUP_RECOVERY_AUTHORITY_INVALID"
        else:
            raise AssertionError("credential-bearing backup was accepted")


def test_composite_output_is_private_no_clobber() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        path = root / "summary.json"
        lock.write_no_clobber(path, _aggregate())
        assert path.stat().st_mode & 0o777 == 0o600
        lock.validate_aggregate_output(json.loads(path.read_text(encoding="utf-8")))
        try:
            lock.write_no_clobber(path, _aggregate())
        except lock.Phase1EFPretransferError as exc:
            assert str(exc) == "PRETRANSFER_OUTPUT_COLLISION"
        else:
            raise AssertionError("existing composite was overwritten")


def test_build_binds_all_six_private_authorities_without_paths() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        paths: dict[str, Path] = {}
        for role in lock.AUTHORITY_ROLES:
            path = root / f"{role}.json"
            path.write_text("{}\n", encoding="utf-8")
            path.chmod(0o600)
            paths[role] = path
        with mock.patch.object(lock, "_validate_checkout", return_value=root), mock.patch.object(
            lock, "_validate_capacity", return_value=None
        ), mock.patch.object(lock, "_validate_backup", return_value=None), mock.patch.object(
            lock,
            "_validate_write_bindings",
            return_value={key: True for key in lock.WRITE_BINDING_KEYS},
        ):
            value = lock.build(
                governing_commit="a" * 40,
                attempt_id="lvef_multitask_phase1ef_post_reallocation_lock_attempt_001",
                checkout_root=root,
                capacity_receipt=paths["post_reallocation_capacity_receipt"],
                capacity_aggregate=paths["post_reallocation_capacity_aggregate"],
                backup_manifest=paths["backup_manifest"],
                backup_aggregate=paths["backup_recovery_aggregate"],
                restore_receipt=paths["restore_test_receipt"],
                future_command=paths["future_first_batch_command"],
                write_binding_gates={key: True for key in lock.WRITE_BINDING_KEYS},
            )
        assert set(value["authority"]) == lock.AUTHORITY_ROLES
        serialized = json.dumps(value)
        assert str(root) not in serialized
