from __future__ import annotations

# SYNTHETIC_CONTROL_PLANE_ONLY: no cloud, qsub, DICOM, GPU, or credential read.

from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_canary_execution_authority as authority
import lvef_c3_canary_manifest as manifest_contract


COMMIT = "a" * 40
RUN_ID = "lvef_c3_exact_five_canary_ab12cd34"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: bytes, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)
    return path


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path), "file_sha256": _sha(path.read_bytes())}


class SyntheticAuthority:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.private = self.root / "owner_private" / "exact_five_canary"
        self.private.mkdir(parents=True, mode=0o700)
        self.private.chmod(0o700)
        self.runs = self.private / "canary_runs"
        self.runs.mkdir(mode=0o700)
        self.public = self.root / "public"
        self.public.mkdir(mode=0o700)

        self.contract = _write(self.public / "contract.yaml", b"synthetic contract\n", 0o600)
        self.execution_state = _write(self.public / "state.json", b"synthetic state\n", 0o600)
        self.state_machine = _write(self.public / "state-machine.json", b"synthetic state machine\n", 0o600)
        self.resume_schema = _write(self.public / "resume-schema.json", b"synthetic resume schema\n", 0o600)
        self.checkpoint = _write(self.public / "checkpoint.pt", b"synthetic checkpoint\n", 0o600)
        self.stage_worker = _write(self.public / "worker.py", b"synthetic worker\n", 0o600)
        self.stage_launcher = _write(self.public / "launcher.sh", b"#!/bin/sh\nexit 0\n", 0o700)
        self.qsub = _write(self.public / "qsub", b"#!/bin/sh\nexit 0\n", 0o700)
        self.gcloud = _write(self.public / "gcloud", b"#!/bin/sh\nexit 0\n", 0o700)
        self.crc_python = _write(self.public / "crc-python", b"#!/bin/sh\nexit 0\n", 0o700)
        self.crc_worker = _write(self.public / "crc-worker.py", b"synthetic crc worker\n", 0o600)

        self.environment = _write(self.private / "environment.json", b"synthetic environment\n")
        self.gcloud_receipt = _write(self.private / "gcloud-receipt.json", b"synthetic gcloud receipt\n")
        self.cloudsdk = self.private / "cloudsdk"
        self.cloudsdk.mkdir(mode=0o700)
        _write(
            self.cloudsdk / "application_default_credentials.json",
            b"credential bytes must not be opened by loader\n",
        )

        configuration = {
            "execution_state": _sha(self.execution_state.read_bytes()),
            "production_contract": _sha(self.contract.read_bytes()),
            "source_metadata": "1" * 64,
            "split_map": "2" * 64,
            "checkpoint": _sha(self.checkpoint.read_bytes()),
            "environment_receipt": _sha(self.environment.read_bytes()),
            "state_machine_schema": _sha(self.state_machine.read_bytes()),
            "resume_ledger_schema": _sha(self.resume_schema.read_bytes()),
            "gcloud_resolution_receipt": _sha(self.gcloud_receipt.read_bytes()),
            "gcloud_executable": _sha(self.gcloud.read_bytes()),
            "crc32c_python_executable": _sha(self.crc_python.read_bytes()),
            "crc32c_worker": _sha(self.crc_worker.read_bytes()),
            "crc32c_distribution": "3" * 64,
        }
        candidates = [
            {
                "study_id": str(910000 + index),
                "subject_id": str(810000 + index),
                "split": "train",
                "expected_object_count": 1,
                "expected_byte_total": index,
                "known_no_cine": False,
                "prior_reconstruction_smoke": False,
            }
            for index in range(1, 6)
        ]
        selected = manifest_contract.select_exact_five(candidates)
        objects = []
        for index, study in enumerate(selected, start=1):
            relative = (
                f"files/p{int(study.subject_id) // 1_000_000:02d}/p{study.subject_id}/"
                f"s{study.study_id}/synthetic_{index}.dcm"
            )
            objects.append(
                {
                    "subject_id": study.subject_id,
                    "study_id": study.study_id,
                    "split": "train",
                    "source_object_key": hashlib.sha256(
                        f"{manifest_contract.SOURCE_RELEASE}\0{relative}".encode()
                    ).hexdigest(),
                    "source_relative_path": relative,
                    "size_bytes": index,
                    "generation": str(index),
                    "md5_base64": "AAAAAAAAAAAAAAAAAAAAAA==",
                    "crc32c_base64": "AAAAAA==",
                }
            )
        self.manifest_value = manifest_contract.build_sealed_manifest(
            selected_studies=selected,
            source_objects=objects,
            source_authority_commit=COMMIT,
            source_manifest_sha256="4" * 64,
            source_configuration_hashes=configuration,
        )
        self.manifest = _write(
            self.private / "manifest.json",
            manifest_contract.serialize_manifest(self.manifest_value),
        )

        plan_authority = {
            "git_commit": COMMIT,
            "orchestration_contract_sha256": configuration["production_contract"],
            "selected_manifest_sha256": self.manifest_value["manifest_sha256"],
            "selected_source_manifest_sha256": "4" * 64,
            "source_metadata_sha256": configuration["source_metadata"],
            "split_map_sha256": configuration["split_map"],
            "checkpoint_sha256": configuration["checkpoint"],
            "environment_receipt_sha256": configuration["environment_receipt"],
            "state_machine_schema_sha256": configuration["state_machine_schema"],
            "resume_ledger_schema_sha256": configuration["resume_ledger_schema"],
            "gcloud_resolution_receipt_sha256": configuration["gcloud_resolution_receipt"],
            "gcloud_executable_sha256": configuration["gcloud_executable"],
            "crc32c_python_executable_sha256": configuration["crc32c_python_executable"],
            "crc32c_worker_sha256": configuration["crc32c_worker"],
            "crc32c_distribution_sha256": configuration["crc32c_distribution"],
        }
        self.plan_value = {"authority": plan_authority, "synthetic": True}
        self.plan_sha = authority.core.canonical_json_sha256(self.plan_value)
        self.plan = _write(
            self.private / "batch-plan.json",
            authority.canonical_json_bytes(self.plan_value) + b"\n",
        )
        self.scheduler_value = {
            "canary_manifest_sha256": self.manifest_value["manifest_sha256"]
        }
        self.scheduler_sha = authority.core.canonical_json_sha256(self.scheduler_value)
        self.scheduler = _write(
            self.private / "scheduler.json",
            authority.canonical_json_bytes(self.scheduler_value) + b"\n",
        )
        self.body_value = {"synthetic_body_authorization": True}
        self.body = _write(
            self.private / "body-transfer.json",
            authority.canonical_json_bytes(self.body_value) + b"\n",
        )
        grants = {
            "DOWNLOAD": {
                "stage_id": "DOWNLOAD",
                **_binding(self.body),
                "authorized": True,
            }
        }
        stage_authorizations = self.private / "stage-authorizations"
        stage_authorizations.mkdir(mode=0o700)
        stage_roles = {
            "DICOM_EXTRACTION": ("DICOM_EXTRACTION", "c3_batch_000"),
            "ECHOPRIME_EMBEDDING": ("ECHOPRIME_EMBEDDING", "c3_batch_000"),
            "BATCH_PRESERVATION": ("BATCH_PRESERVATION", "c3_batch_000"),
            "CANARY_FINALIZATION": ("PRESERVATION_FINALIZATION", "all_batches"),
        }
        for stage, (authorization_stage, batch_id) in stage_roles.items():
            receipt = {
                "schema_version": 1,
                "artifact_type": "lvef_c3_restricted_stage_authorization_v1",
                "status": "AUTHORIZED",
                "authorization_scope": authority.stages.STAGE_AUTHORIZATION_SCOPES[
                    authorization_stage
                ],
                "stage": authorization_stage,
                "batch_id": batch_id,
                "attempt_id": RUN_ID,
                "governing_commit": COMMIT,
                "orchestration_contract_sha256": configuration[
                    "production_contract"
                ],
                "batch_plan_sha256": self.plan_sha,
                "launch_authority_sha256": "5" * 64,
                "owner_authorized": True,
                "owner_authorization_date": "2026-08-12",
            }
            stage_path = _write(
                stage_authorizations / f"{stage}.json",
                authority.canonical_json_bytes(receipt) + b"\n",
            )
            grants[stage] = {
                "stage_id": stage,
                **_binding(stage_path),
                "authorized": True,
            }
        runtime = {**plan_authority, "batch_plan_sha256": self.plan_sha}
        self.packet = {
            "schema_version": 1,
            "artifact_type": authority.ARTIFACT_TYPE,
            "status": authority.STATUS,
            "governing_commit": COMMIT,
            "branch": authority.BRANCH,
            "run_id": RUN_ID,
            "attempt_id": RUN_ID,
            "output_root": str(self.runs / RUN_ID),
            "manifest": {
                **_binding(self.manifest),
                "embedded_sha256": self.manifest_value["manifest_sha256"],
            },
            "batch_plan": {**_binding(self.plan), "canonical_sha256": self.plan_sha},
            "scheduler_plan": {
                **_binding(self.scheduler),
                "canonical_sha256": self.scheduler_sha,
            },
            "production_contract": _binding(self.contract),
            "environment_receipt": _binding(self.environment),
            "checkpoint": _binding(self.checkpoint),
            "runtime_authority": runtime,
            "hard_scope": copy.deepcopy(authority.HARD_SCOPE),
            "scheduler": copy.deepcopy(authority.SCHEDULER_SCOPE),
            "stage_authorizations": grants,
            "body_transfer_authorization": _binding(self.body),
            "launch_authority_sha256": "5" * 64,
            "gcloud": {
                "binary_path": str(self.gcloud),
                "resolution_receipt_path": str(self.gcloud_receipt),
                "cloudsdk_config_path": str(self.cloudsdk),
            },
            "crc32c": {
                "python_path": str(self.crc_python),
                "worker_path": str(self.crc_worker),
            },
            "owner_authorized": True,
            "authorization_scopes": copy.deepcopy(authority.AUTHORIZATION_SCOPES),
            "qsub": _binding(self.qsub),
            "stage_worker": _binding(self.stage_worker),
            "stage_launcher": _binding(self.stage_launcher),
            "requester_pays": {
                "billing_environment_variable": "LVEF_C3_GCP_BILLING_PROJECT",
                "billing_project": "synthetic-private-project",
            },
        }
        self.authorization = self.private / "execution_authorization_v1.json"
        self.write_packet()

    def write_packet(self, *, canonical: bool = True) -> None:
        self.packet.pop("authorization_sha256", None)
        self.packet["authorization_sha256"] = authority.calculate_authorization_sha256(
            self.packet
        )
        payload = (
            authority.serialize_authorization(self.packet)
            if canonical
            else (json.dumps(self.packet, indent=2, sort_keys=True) + "\n").encode()
        )
        _write(self.authorization, payload)

    def patches(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            mock.patch.multiple(
                authority,
                PRIVATE_ROOT=self.private,
                FIXED_PATH=self.authorization,
                CANARY_RUN_ROOT=self.runs,
                TRACKED_WORKTREE=self.public,
                PRODUCTION_CONTRACT_PATH=self.contract,
                EXECUTION_STATE_PATH=self.execution_state,
                STATE_MACHINE_PATH=self.state_machine,
                RESUME_LEDGER_PATH=self.resume_schema,
                STAGE_WORKER_PATH=self.stage_worker,
                STAGE_LAUNCHER_PATH=self.stage_launcher,
            )
        )
        stack.enter_context(
            mock.patch.object(
                authority.core, "validate_batch_plan", return_value=self.plan_sha
            )
        )
        stack.enter_context(
            mock.patch.object(
                authority.core,
                "load_orchestration_contract",
                return_value={"downloader": {"maximum_attempts_per_object": 5}},
            )
        )
        stack.enter_context(
            mock.patch.object(
                authority.core,
                "initialize_resume_ledger",
                return_value={
                    "attempt_id": RUN_ID,
                    "authority": self.packet["runtime_authority"],
                },
            )
        )
        self.body_validator = stack.enter_context(
            mock.patch.object(
                authority.core,
                "validate_body_transfer_authorization",
                return_value=None,
            )
        )
        stack.enter_context(
            mock.patch.object(
                authority.scheduler_contract,
                "load_scheduler_plan",
                return_value=self.scheduler_value,
            )
        )
        stack.enter_context(
            mock.patch.object(
                authority.scheduler_contract,
                "validate_scheduler_plan",
                return_value=self.scheduler_sha,
            )
        )
        return stack


def _expect(code: str, operation) -> None:
    try:
        operation()
    except authority.CanaryExecutionAuthorityError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"expected {code}")


def test_synthetic_closed_authority_passes_and_returns_worker_mapping() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = SyntheticAuthority(Path(directory))
        with fixture.patches():
            result = authority.load_and_validate_execution_authority(
                fixture.authorization, expected_governing_commit=COMMIT
            )
        assert result["authorization_path"] == str(fixture.authorization)
        assert result["authorization_sha256"] == fixture.packet["authorization_sha256"]
        assert result["authorization_file_sha256"] == _sha(
            fixture.authorization.read_bytes()
        )
        assert result["runtime_authority"]["batch_plan_sha256"] == fixture.plan_sha
        assert result["scheduler"]["scheduler_submission_count"] == 5
        assert result["scheduler"]["stage_retry_count"] == 0
        assert result["authorization_scopes"]["model_fitting"] is False
        assert result["authorization_scopes"][
            "canonical_execution_state_execute_permission_required"
        ] is True
        fixture.body_validator.assert_called_once()


def test_authority_rejects_noncanonical_serialization_and_unknown_key() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = SyntheticAuthority(Path(directory))
        fixture.write_packet(canonical=False)
        with fixture.patches():
            _expect(
                "CANARY_EXECUTION_AUTHORITY_SERIALIZATION_NOT_CANONICAL",
                lambda: authority.load_and_validate_execution_authority(
                    fixture.authorization
                ),
            )
        fixture.packet["unexpected"] = False
        fixture.write_packet()
        with fixture.patches():
            _expect(
                "CANARY_EXECUTION_AUTHORITY_SCHEMA_NOT_CLOSED",
                lambda: authority.load_and_validate_execution_authority(
                    fixture.authorization
                ),
            )


def test_authority_rejects_scope_drift_file_tampering_and_output_collision() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = SyntheticAuthority(Path(directory))
        fixture.packet["scheduler"]["scheduler_submission_count"] = 6
        fixture.write_packet()
        with fixture.patches():
            _expect(
                "CANARY_EXECUTION_SCHEDULER_SCOPE_INVALID",
                lambda: authority.load_and_validate_execution_authority(
                    fixture.authorization
                ),
            )
    with tempfile.TemporaryDirectory() as directory:
        fixture = SyntheticAuthority(Path(directory))
        fixture.body.write_bytes(b"changed after sealing\n")
        fixture.body.chmod(0o600)
        with fixture.patches():
            _expect(
                "CANARY_EXECUTION_PRIVATE_FILE_HASH_MISMATCH",
                lambda: authority.load_and_validate_execution_authority(
                    fixture.authorization
                ),
            )
    with tempfile.TemporaryDirectory() as directory:
        fixture = SyntheticAuthority(Path(directory))
        Path(fixture.packet["output_root"]).mkdir(mode=0o700)
        with fixture.patches():
            _expect(
                "CANARY_EXECUTION_OUTPUT_ROOT_COLLISION",
                lambda: authority.load_and_validate_execution_authority(
                    fixture.authorization
                ),
            )
            result = authority.load_and_validate_execution_authority(
                fixture.authorization, require_output_absent=False
            )
        assert result["run_id"] == RUN_ID


def test_authority_rejects_semantic_grant_drift_and_nonprivate_root() -> None:
    with tempfile.TemporaryDirectory() as directory:
        fixture = SyntheticAuthority(Path(directory))
        grant_path = Path(
            fixture.packet["stage_authorizations"]["DICOM_EXTRACTION"]["path"]
        )
        grant = json.loads(grant_path.read_text())
        grant["attempt_id"] = "lvef_c3_exact_five_canary_deadbeef"
        _write(grant_path, authority.canonical_json_bytes(grant) + b"\n")
        fixture.packet["stage_authorizations"]["DICOM_EXTRACTION"][
            "file_sha256"
        ] = _sha(grant_path.read_bytes())
        fixture.write_packet()
        with fixture.patches():
            _expect(
                "CANARY_EXECUTION_SCIENTIFIC_AUTHORIZATION_INVALID",
                lambda: authority.load_and_validate_execution_authority(
                    fixture.authorization
                ),
            )
    with tempfile.TemporaryDirectory() as directory:
        fixture = SyntheticAuthority(Path(directory))
        fixture.runs.chmod(0o755)
        with fixture.patches():
            _expect(
                "CANARY_EXECUTION_PRIVATE_DIRECTORY_INVALID",
                lambda: authority.load_and_validate_execution_authority(
                    fixture.authorization
                ),
            )


def test_authority_schema_is_closed_and_matches_runtime_packet_keys() -> None:
    schema = json.loads(
        (ROOT / "configs/lvef_c3_canary_execution_authority_schema_v1.json").read_text()
    )
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(authority.PACKET_KEYS)
    assert set(schema["properties"]) == set(authority.PACKET_KEYS)
    assert schema["properties"]["owner_authorized"]["const"] is True
    assert schema["properties"]["scheduler"]["additionalProperties"] is False
    assert schema["properties"]["stage_authorizations"]["additionalProperties"] is False
    assert schema["$defs"]["runtimeAuthority"]["additionalProperties"] is False
    assert schema["$defs"]["authorizationScopes"]["additionalProperties"] is False
