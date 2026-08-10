from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_lvef_c3_production_authority_packet as packet
import lvef_multitask_analysis_modes as analysis_modes


def _artifacts(root: Path) -> dict[str, Path]:
    values: dict[str, Path] = {}
    for index, role in enumerate(sorted(packet.REQUIRED_ROLES)):
        path = root / f"authority_{index:02d}.txt"
        path.write_text(f"synthetic authority {role}\n", encoding="utf-8")
        values[role] = path
    return values


def test_authority_packet_is_closed_unexecuted_and_path_free() -> None:
    assert len(packet.REQUIRED_ROLES) == 38
    assert len(packet.SEMANTIC_VALIDATION_KEYS) == 17
    assert packet.TRACKED_ROLE_PATHS["crc32c_worker"] == (
        "scripts/lvef_c3_crc32c_worker.py"
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        value = packet.build_packet(
            governing_commit="a" * 40,
            attempt_id="lvef_c3_phase1ee_synthetic_001",
            artifacts=_artifacts(root),
            semantic_validation={
                key: True for key in packet.SEMANTIC_VALIDATION_KEYS
            },
        )
        packet.validate_packet(value)
        text = json.dumps(value)
        assert str(root) not in text
        assert all(item is False for item in value["authorization_scopes"].values())
        assert value["execution_attestations"]["cloud_requests"] == 0
        assert value["execution_attestations"]["scheduler_jobs_submitted"] == 0
        policy, _ = analysis_modes.load_policy(
            ROOT / "configs" / "lvef_multitask_safe_export_policy.yaml"
        )
        result = analysis_modes.validate_candidate_bytes(
            (json.dumps(value, sort_keys=True) + "\n").encode("utf-8"),
            filename="lvef_c3_production_authority_packet.summary.json",
            profile_name="phase1ee_production_authority_packet_json",
            policy=policy,
        )
        assert result["status"] == "PASS"


def test_authority_packet_rejects_missing_and_duplicate_roles() -> None:
    with tempfile.TemporaryDirectory() as directory:
        artifacts = _artifacts(Path(directory))
        artifacts.pop(next(iter(artifacts)))
        try:
            packet.build_packet(
                governing_commit="b" * 40,
                attempt_id="lvef_c3_phase1ee_synthetic_002",
                artifacts=artifacts,
                semantic_validation={
                    key: True for key in packet.SEMANTIC_VALIDATION_KEYS
                },
            )
        except packet.AuthorityPacketError as exc:
            assert str(exc) == "ARTIFACT_ROLE_SET_NOT_EXACT"
        else:
            raise AssertionError("missing authority role was accepted")


def test_authority_packet_rejects_symlink_and_no_clobber() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        artifacts = _artifacts(root)
        role = next(iter(artifacts))
        target = artifacts[role]
        link = root / "link"
        link.symlink_to(target)
        artifacts[role] = link
        try:
            packet.build_packet(
                governing_commit="c" * 40,
                attempt_id="lvef_c3_phase1ee_synthetic_003",
                artifacts=artifacts,
                semantic_validation={
                    key: True for key in packet.SEMANTIC_VALIDATION_KEYS
                },
            )
        except packet.AuthorityPacketError as exc:
            assert str(exc) == "AUTHORITY_NOT_REGULAR_NOFOLLOW_FILE"
        else:
            raise AssertionError("symlink authority was accepted")

        output = root / "packet.json"
        output.write_text("occupied\n", encoding="utf-8")
        try:
            packet.write_exclusive_json(output, {"status": "synthetic"})
        except packet.AuthorityPacketError as exc:
            assert str(exc) == "OUTPUT_ALREADY_EXISTS"
        else:
            raise AssertionError("existing output was overwritten")


def test_state_resume_and_preservation_configs_are_unambiguous() -> None:
    state = json.loads((ROOT / "configs" / "lvef_c3_state_machine_v2.json").read_text())
    resume = json.loads((ROOT / "configs" / "lvef_c3_resume_ledger_v2.json").read_text())
    assert len(state["states"]) == len(set(state["states"])) == 13
    assert state["initial_state"] == "PLANNED"
    assert state["additionalProperties"] is False
    assert resume["no_clobber"] is True
    assert resume["stale_or_partial_artifact_may_promote_state"] is False
    assert len(resume["authority_fields"]) == len(set(resume["authority_fields"]))


def test_packet_cannot_claim_pass_without_every_semantic_validation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        validations = {key: True for key in packet.SEMANTIC_VALIDATION_KEYS}
        validations["batch_plan_exact_rederivation_verified"] = False
        try:
            packet.build_packet(
                governing_commit="d" * 40,
                attempt_id="lvef_c3_phase1ee_synthetic_004",
                artifacts=_artifacts(Path(directory)),
                semantic_validation=validations,
            )
        except packet.AuthorityPacketError as exc:
            assert str(exc) == "SEMANTIC_VALIDATION_NOT_ALL_PASS"
        else:
            raise AssertionError("packet accepted an unverified semantic authority")


def test_gcloud_resolution_authority_is_closed_and_binary_bound() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        executable = root / "google-cloud-sdk" / "bin" / "gcloud"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
        executable_sha = hashlib.sha256(executable.read_bytes()).hexdigest()
        receipt = root / "resolution.json"
        payload = {
            "audit": "lvef_scc_gcloud_resolution",
            "credential_material_accessed": False,
            "expected_version": packet.PINNED_GCLOUD_VERSION,
            "executable_sha256": executable_sha,
            "module_name": None,
            "resolution_source": "COMMON_SELF_CONTAINED_INSTALL",
            "retained_archive_sha256": packet.PINNED_GCLOUD_ARCHIVE_SHA256,
            "retained_tar_payload_sha256": packet.PINNED_GCLOUD_TAR_PAYLOAD_SHA256,
            "selected_executable": str(executable.resolve()),
            "status": "PASS",
            "version": packet.PINNED_GCLOUD_VERSION,
        }
        receipt.write_text(json.dumps(payload), encoding="utf-8")
        original_root = packet.PINNED_GCLOUD_ROOT
        try:
            packet.PINNED_GCLOUD_ROOT = str(root)
            packet._validate_gcloud_resolution_authority(receipt, executable)
            payload["version"] = "578.0.0"
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            try:
                packet._validate_gcloud_resolution_authority(receipt, executable)
            except packet.AuthorityPacketError as exc:
                assert str(exc) == "GCLOUD_RESOLUTION_RECEIPT_NOT_AUTHORITATIVE"
            else:
                raise AssertionError("wrong gcloud version was accepted")
        finally:
            packet.PINNED_GCLOUD_ROOT = original_root


def test_execution_environment_parser_is_closed_private_and_duplicate_safe() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "execution.env"
        lines = [f"{key}=synthetic" for key in sorted(packet.RUNTIME_ENVIRONMENT_KEYS)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o600)
        assert set(packet._parse_execution_environment(path)) == set(
            packet.RUNTIME_ENVIRONMENT_KEYS
        )
        path.write_text("\n".join(lines + [lines[0]]) + "\n", encoding="utf-8")
        try:
            packet._parse_execution_environment(path)
        except packet.AuthorityPacketError as exc:
            assert str(exc) == "EXECUTION_ENVIRONMENT_KEY_DUPLICATE"
        else:
            raise AssertionError("duplicate execution environment key was accepted")


def test_split_crc32c_runtime_is_closed_across_packet_and_future_commands() -> None:
    for key in (
        "LVEF_C3_CRC32C_PYTHON",
        "LVEF_C3_CRC32C_PYTHON_SHA256",
        "LVEF_C3_CRC32C_WORKER",
        "LVEF_C3_CRC32C_WORKER_SHA256",
        "LVEF_C3_CRC32C_DISTRIBUTION_SHA256",
    ):
        assert key in packet.RUNTIME_ENVIRONMENT_KEYS
    commands = (ROOT / "docs/lvef_multitask/scc_phase1ee_production_commands.md").read_text(
        encoding="utf-8"
    )
    assert "Exactly all 38 closed authority roles" in commands
    assert '--artifact "crc32c_python_executable=$CRC32C_PY"' in commands
    assert '--artifact "crc32c_worker=$CRC32C_WORKER"' in commands
    assert '--crc32c-python "$CRC32C_PY"' in commands
    assert '--crc32c-worker "$CRC32C_WORKER"' in commands
    runner = (ROOT / "scripts/scc_run_lvef_c3_production_batch_v2.sh").read_text(
        encoding="utf-8"
    )
    assert '--crc32c-python "$LVEF_C3_CRC32C_PYTHON"' in runner
    assert '--crc32c-worker "$LVEF_C3_CRC32C_WORKER"' in runner


def test_authority_packet_rejects_symlinked_ancestor_for_read_and_output() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        real = root / "real"
        real.mkdir()
        real.chmod(0o700)
        target = real / "authority.json"
        target.write_text("{}\n", encoding="utf-8")
        target.chmod(0o600)
        link = root / "linked_parent"
        link.symlink_to(real, target_is_directory=True)
        try:
            packet.read_regular_nofollow(link / target.name)
        except packet.AuthorityPacketError as exc:
            assert str(exc) == "AUTHORITY_PATH_SYMLINK_ANCESTOR"
        else:
            raise AssertionError("Authority read followed a symlinked ancestor")
        try:
            packet.write_exclusive_json(link / "new.json", {"status": "PASS"})
        except packet.AuthorityPacketError as exc:
            assert str(exc) == "AUTHORITY_PATH_SYMLINK_ANCESTOR"
        else:
            raise AssertionError("Authority output followed a symlinked ancestor")
