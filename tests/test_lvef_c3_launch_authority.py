from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_lvef_c3_production_launch_authority as launch


def _private(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _capacity(*, backed: bool = True) -> dict[str, object]:
    value: dict[str, object] = {key: True for key in launch.PASS_CAPACITY_KEYS}
    value.update(
        {
            "backed_control_tier_gate_passed": backed,
            "full_c3_authorized": False,
            "dicom_body_transfer_authorized": False,
            "cloud_requests": 0,
            "object_bodies_downloaded": 0,
            "quota_changed": False,
            "files_moved": 0,
            "files_deleted": 0,
        }
    )
    return value


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
        ), mock.patch.object(
            launch.capacity, "validate_aggregate_output", return_value=None
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
            launch.validate(
                observed,
                envelope_path=envelope,
                attempt_id="lvef_c3_phase1ee_synthetic_001",
                governing_commit="a" * 40,
                execution_environment=environment,
            )
            capacity_path.write_text("{}\n", encoding="utf-8")
            try:
                launch.validate(
                    observed,
                    envelope_path=envelope,
                    attempt_id="lvef_c3_phase1ee_synthetic_001",
                    governing_commit="a" * 40,
                    execution_environment=environment,
                )
            except launch.LaunchAuthorityError as exc:
                assert exc.code == "LAUNCH_FILE_BINDING_MISMATCH"
            else:
                raise AssertionError("Tampered bound capacity evidence was accepted")


def test_launch_authority_rejects_failed_backed_control_gate() -> None:
    value = _capacity(backed=False)
    with mock.patch.object(
        launch.capacity, "validate_aggregate_output", return_value=None
    ):
        try:
            launch._validate_capacity(value)
        except launch.LaunchAuthorityError as exc:
            assert exc.code == "PRETRANSFER_CAPACITY_GATE_NOT_PASS"
        else:
            raise AssertionError("Failed backed-control gate was launch-authorized")


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
