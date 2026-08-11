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

import build_lvef_c3_production_launch_authority as launch
import build_lvef_c3_phase1ef_pretransfer_lock as pretransfer


def _private(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _capacity(*, backed: bool = True) -> dict[str, object]:
    capacity_gates = {key: True for key in pretransfer.CAPACITY_GATE_KEYS}
    capacity_gates["backed_control_tier_gate_passed"] = backed
    return {
        "schema_version": pretransfer.SCHEMA_VERSION,
        "artifact_type": pretransfer.ARTIFACT_TYPE,
        "status": pretransfer.STATUS,
        "attempt_id": "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001",
        "governing_commit": "a" * 40,
        "created_at_utc": "2026-08-11T12:00:00Z",
        "authority": {
            role: {"size_bytes": 1, "sha256": "b" * 64}
            for role in pretransfer.AUTHORITY_ROLES
        },
        "capacity_gates": capacity_gates,
        "backup_recovery_gates": {
            key: True for key in pretransfer.BACKUP_GATE_KEYS
        },
        "write_binding_gates": {
            key: True for key in pretransfer.WRITE_BINDING_KEYS
        },
        "execution_attestations": dict(pretransfer.EXECUTION_ATTESTATIONS),
        "authorization_scopes_granted": 0,
        "full_c3_status": "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION",
    }


def _packet(environment_sha: str, capacity_sha: str) -> dict[str, object]:
    return {
        "attempt_id": "lvef_c3_phase1ee_synthetic_001",
        "governing_commit": "a" * 40,
        "full_c3_status": "GO_PENDING_EXPLICIT_OWNER_AUTHORIZATION",
        "authority": {
            "execution_environment": {"sha256": environment_sha},
            "post_expansion_capacity_summary": {"sha256": capacity_sha},
        },
        "authorization_scopes": {"first_batch_dicom_body_transfer": False},
    }


def test_launch_authority_is_last_non_circular_binding_and_detects_tamper() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        environment = _private(root / "execution.env", {"synthetic": True})
        capacity_path = _private(root / "capacity.json", _capacity())
        authority_packet = _private(
            root / "packet.json",
            _packet(launch.sha256_file(environment), launch.sha256_file(capacity_path)),
        )
        parsed_environment = {
            "LVEF_C3_ATTEMPT_ID": "lvef_c3_phase1ee_synthetic_001",
            "LVEF_C3_GOVERNING_COMMIT": "a" * 40,
        }
        with mock.patch.object(
            launch.packet, "_parse_execution_environment", return_value=parsed_environment
        ), mock.patch.object(launch.packet, "validate_packet", return_value=None):
            value = launch.build(
                attempt_id="lvef_c3_phase1ee_synthetic_001",
                governing_commit="a" * 40,
                execution_environment=environment,
                capacity_summary=capacity_path,
                authority_packet=authority_packet,
            )
            envelope = root / "launch.restricted.json"
            launch.write_no_clobber(envelope, value)
            observed = launch._load_json(envelope, "LAUNCH_AUTHORITY")
            try:
                launch.validate(
                    observed,
                    envelope_path=envelope,
                    attempt_id="lvef_c3_phase1ee_synthetic_001",
                    governing_commit="a" * 40,
                    execution_environment=environment,
                )
            except launch.LaunchAuthorityError as exc:
                assert exc.code == "TERMINAL_FINAL_LOCK_NOT_AUTHORITATIVE"
            else:
                raise AssertionError("launch validated without terminal final lock")
            launch.validate(
                observed,
                envelope_path=envelope,
                attempt_id="lvef_c3_phase1ee_synthetic_001",
                governing_commit="a" * 40,
                execution_environment=environment,
                require_terminal_lock=False,
            )
            capacity_path.write_text("{}\n", encoding="utf-8")
            try:
                launch.validate(
                    observed,
                    envelope_path=envelope,
                    attempt_id="lvef_c3_phase1ee_synthetic_001",
                    governing_commit="a" * 40,
                    execution_environment=environment,
                    require_terminal_lock=False,
                )
            except launch.LaunchAuthorityError as exc:
                assert exc.code == "LAUNCH_FILE_BINDING_MISMATCH"
            else:
                raise AssertionError("Tampered bound capacity evidence was accepted")


def test_launch_authority_rejects_failed_backed_control_gate() -> None:
    value = _capacity(backed=False)
    try:
        launch._validate_capacity(value, governing_commit="a" * 40)
    except launch.LaunchAuthorityError as exc:
        assert exc.code == "CAPACITY_SUMMARY_NOT_AUTHORITATIVE"
    else:
        raise AssertionError("Failed backed-control gate was launch-authorized")


def test_launch_authority_rejects_mixed_pretransfer_commit() -> None:
    value = _capacity()
    try:
        launch._validate_capacity(value, governing_commit="c" * 40)
    except launch.LaunchAuthorityError as exc:
        assert exc.code == "PRETRANSFER_CAPACITY_GATE_NOT_PASS"
    else:
        raise AssertionError("mixed-commit pretransfer authority was accepted")


def test_default_launch_validation_requires_terminal_recovery_seal() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        environment = _private(root / "execution.env", {"synthetic": True})
        capacity_path = _private(root / "capacity.json", _capacity())
        authority_packet = _private(
            root / "packet.json",
            _packet(launch.sha256_file(environment), launch.sha256_file(capacity_path)),
        )
        parsed_environment = {
            "LVEF_C3_ATTEMPT_ID": "lvef_c3_phase1ee_synthetic_001",
            "LVEF_C3_GOVERNING_COMMIT": "a" * 40,
        }
        with mock.patch.object(
            launch.packet, "_parse_execution_environment", return_value=parsed_environment
        ), mock.patch.object(launch.packet, "validate_packet", return_value=None):
            value = launch.build(
                attempt_id="lvef_c3_phase1ee_synthetic_001",
                governing_commit="a" * 40,
                execution_environment=environment,
                capacity_summary=capacity_path,
                authority_packet=authority_packet,
            )
            envelope = root / "launch.restricted.json"
            launch.write_no_clobber(envelope, value)
            final_validate = mock.Mock()
            terminal_validate = mock.Mock()

            def importer(name: str):
                if name == "finalize_lvef_c3_phase1ef_pretransfer_lock":
                    return SimpleNamespace(validate_terminal_for_launch=final_validate)
                if name == "build_lvef_c3_terminal_recovery_seal":
                    return SimpleNamespace(validate_terminal_for_launch=terminal_validate)
                raise AssertionError(name)

            with mock.patch.object(launch.importlib, "import_module", side_effect=importer), mock.patch.object(
                launch, "TERMINAL_RECOVERY_BACKED_PREFIX", root
            ):
                launch.validate(
                    value,
                    envelope_path=envelope,
                    attempt_id="lvef_c3_phase1ee_synthetic_001",
                    governing_commit="a" * 40,
                    execution_environment=environment,
                )
            final_validate.assert_called_once()
            terminal_validate.assert_called_once()
            assert terminal_validate.call_args.args[0].resolve() == (
                root
                / "lvef_multitask_phase1ef_post_reallocation_lock_attempt_001"
                / "terminal_recovery_seal"
                / launch.TERMINAL_RECOVERY_SEAL_FILENAME
            ).resolve()

            terminal_validate.reset_mock()
            terminal_validate.side_effect = RuntimeError("synthetic tamper")
            with mock.patch.object(launch.importlib, "import_module", side_effect=importer), mock.patch.object(
                launch, "TERMINAL_RECOVERY_BACKED_PREFIX", root
            ):
                try:
                    launch.validate(
                        value,
                        envelope_path=envelope,
                        attempt_id="lvef_c3_phase1ee_synthetic_001",
                        governing_commit="a" * 40,
                        execution_environment=environment,
                    )
                except launch.LaunchAuthorityError as exc:
                    assert exc.code == "TERMINAL_FINAL_LOCK_NOT_AUTHORITATIVE"
                else:
                    raise AssertionError("tampered terminal recovery seal was accepted")


def test_launch_authority_schema_is_closed_and_owner_scopes_remain_false() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        envelope = _private(root / "launch.json", {})
        value = {key: None for key in launch.TOP_LEVEL_KEYS}
        value["unexpected"] = False
        try:
            launch.validate(
                value,
                envelope_path=envelope,
                attempt_id="lvef_c3_phase1ee_synthetic_001",
                governing_commit="a" * 40,
                execution_environment=envelope,
            )
        except launch.LaunchAuthorityError as exc:
            assert exc.code == "LAUNCH_AUTHORITY_SCHEMA_NOT_CLOSED"
        else:
            raise AssertionError("Unknown launch-authority field was accepted")
