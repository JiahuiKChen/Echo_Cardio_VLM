from __future__ import annotations

import base64
import copy
import csv
import hashlib
import io
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_minimal_canary as minimal
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as stages


def _fixture_module():
    path = ROOT / "tests/test_lvef_c3_canary_integration.py"
    spec = importlib.util.spec_from_file_location("minimal_canary_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mocked_torch_modules(fixture: ModuleType, calls: dict[str, int]) -> dict[str, ModuleType]:
    """Return CPU-only stand-ins for only the EchoPrime compute boundary."""

    tensor_type = fixture._ArrayTensor
    tensor_type.to = lambda self, _device: self
    tensor_type.detach = lambda self: self
    tensor_type.cpu = lambda self: self
    tensor_type.numpy = lambda self: self.value

    torch = ModuleType("torch")
    torch.__version__ = "synthetic-torch"
    torch.float32 = np.float32
    torch.as_tensor = lambda value, dtype=None: tensor_type(value)
    torch.stack = lambda values, dim=0: tensor_type(
        np.stack([value.value for value in values], axis=dim)
    )
    torch.manual_seed = lambda _seed: None
    torch.use_deterministic_algorithms = lambda _enabled: None
    torch.set_float32_matmul_precision = lambda _value: None
    torch.device = lambda value: value
    torch.load = lambda *_args, **_kwargs: {"synthetic": True}

    class _Cuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def manual_seed_all(_seed: int) -> None:
            return None

    class _Cudnn:
        benchmark = True
        deterministic = False
        allow_tf32 = True

        @staticmethod
        def version() -> str:
            return "synthetic-cudnn"

    torch.cuda = _Cuda()
    torch.version = SimpleNamespace(cuda="synthetic-cuda")
    torch.backends = SimpleNamespace(
        cudnn=_Cudnn(),
        cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=True)),
    )
    torch.nn = SimpleNamespace(
        Linear=lambda in_features, out_features: SimpleNamespace(
            in_features=in_features, out_features=out_features
        )
    )

    class _InferenceMode:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_: Any) -> None:
            return None

    torch.inference_mode = _InferenceMode

    class _FakeEncoder:
        def __init__(self) -> None:
            self.head = [SimpleNamespace(in_features=768)]
            self.offset = 0

        def load_state_dict(self, state: Any, *, strict: bool) -> None:
            assert state == {"synthetic": True}
            assert strict is True

        def eval(self) -> _FakeEncoder:
            return self

        def to(self, _device: str) -> _FakeEncoder:
            return self

        @staticmethod
        def parameters() -> list[SimpleNamespace]:
            return [SimpleNamespace(requires_grad=True)]

        def __call__(self, batch: Any) -> Any:
            calls["mock_encoder_compute"] = calls.get("mock_encoder_compute", 0) + 1
            count = int(batch.value.shape[0])
            rows = np.stack(
                [
                    (
                        np.arange(512, dtype=np.float32)
                        + np.float32(self.offset + index + 1)
                    )
                    / np.float32(1000.0)
                    for index in range(count)
                ]
            )
            self.offset += count
            return tensor_type(rows)

    torchvision = ModuleType("torchvision")
    torchvision.__version__ = "synthetic-torchvision"
    torchvision.models = SimpleNamespace(
        video=SimpleNamespace(mvit_v2_s=lambda *, weights: _FakeEncoder())
    )
    return {"torch": torch, "torchvision": torchvision}


def _write_fixture(path: Path, payload: bytes, mode: int = 0o600) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.chmod(path, mode)
    return path


def _csv_bytes(fieldnames: list[str], rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _synthetic_authority_and_manifest(
    fixture: ModuleType, root: Path
) -> tuple[minimal.LiveAuthority, dict[str, Any], Path, list[dict[str, str]]]:
    """Materialize only sanitized authority files needed by the real builder."""

    authority_root = root / "authority"
    authority_root.mkdir(mode=0o700)
    selected = fixture.manifest_contract.select_exact_five(
        [fixture._candidate(index) for index in range(1, 6)]
    )
    objects = [
        fixture._source_object(study, ordinal)
        for study in selected
        for ordinal in (1, 2)
    ]
    checkpoint = _write_fixture(
        authority_root / "synthetic_checkpoint.bin",
        fixture.integration.SYNTHETIC_CHECKPOINT_BYTES,
    )
    distributions = list(importlib.metadata.distributions())
    by_normalized_name: dict[str, dict[str, str]] = {}
    for distribution in distributions:
        name = distribution.metadata.get("Name")
        if not name:
            continue
        normalized = re.sub(r"[-_.]+", "-", str(name)).casefold()
        by_normalized_name.setdefault(
            normalized,
            {"name": str(name), "version": str(distribution.version)},
        )
    package_inventory = sorted(
        by_normalized_name.values(),
        key=lambda row: (row["name"].casefold(), row["version"]),
    )
    governing_commit = minimal._git("rev-parse", "HEAD")
    environment_payload = fixture.integration.synthetic_environment_receipt(
        governing_commit
    )
    environment_payload.update(
        {
            "python_executable_sha256": stages.resolved_python_executable_sha256(
                Path(sys.executable)
            ),
            "python_version": platform.python_version(),
            "operating_system": platform.platform(),
            "package_inventory": package_inventory,
            "package_inventory_sha256": core.canonical_json_sha256(
                package_inventory
            ),
            "package_count": len(package_inventory),
        }
    )
    environment = _write_fixture(
        authority_root / "synthetic_environment.restricted.json",
        (json.dumps(environment_payload, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    source_rows = [
        {
            "release_id": fixture.manifest_contract.SOURCE_RELEASE,
            "subject_id": row["subject_id"],
            "study_id": row["study_id"],
            "split": "train",
            "source_relative_path": row["source_relative_path"],
            "source_object_key": row["source_object_key"],
        }
        for row in objects
    ]
    metadata_rows = [
        {
            **row,
            "production_batch": minimal.BATCH_ID,
            "remote_size_bytes": row["size_bytes"],
            "remote_md5_base64": row["md5_base64"],
            "remote_crc32c_base64": row["crc32c_base64"],
            "remote_generation": row["generation"],
            "preflight_status": "PASS",
            "discrepancy_reasons": [],
        }
        for row in objects
    ]
    source_metadata = _write_fixture(
        authority_root / "source_metadata.restricted.jsonl",
        b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
            for row in metadata_rows
        ),
    )
    split_map = _write_fixture(
        authority_root / "split_map.restricted.csv",
        _csv_bytes(
            ["subject_id", "split"],
            [
                {"subject_id": study.subject_id, "split": "train"}
                for study in selected
            ],
        ),
    )
    selected_studies = _write_fixture(
        authority_root / "selected_studies.restricted.csv",
        _csv_bytes(
            ["subject_id", "study_id"],
            [
                {"subject_id": study.subject_id, "study_id": study.study_id}
                for study in selected
            ],
        ),
    )
    selected_source = _write_fixture(
        authority_root / "selected_source.restricted.csv",
        _csv_bytes(
            [
                "release_id",
                "subject_id",
                "study_id",
                "split",
                "source_relative_path",
                "source_object_key",
            ],
            source_rows,
        ),
    )
    historical_manifest = _write_fixture(
        authority_root / "historical_study_manifest.csv",
        b"subject_id,study_id\n1,1\n",
    )
    prior_smoke_manifest = _write_fixture(
        authority_root / "prior_smoke_source_manifest.restricted.csv",
        b"subject_id,study_id\n1,1\n",
    )
    prior_smoke_summary = _write_fixture(
        authority_root / "prior_smoke_source_summary.json",
        b'{"status":"SYNTHETIC"}\n',
    )
    prior_smoke_safety = _write_fixture(
        authority_root / "prior_smoke_source_safety.json",
        b'{"status":"SYNTHETIC"}\n',
    )
    prior_smoke_preservation = _write_fixture(
        authority_root / "prior_smoke_preservation_manifest.tsv",
        b"relative_path\tsize_bytes\tsha256\nsynthetic\t1\t"
        + b"7" * 64
        + b"\n",
    )
    gcloud = _write_fixture(
        authority_root / "gcloud", b"#!/bin/sh\nexit 70\n", 0o700
    )
    gcloud_receipt = _write_fixture(
        authority_root / "gcloud_receipt.restricted.json",
        b'{"status":"SYNTHETIC_NO_CLOUD"}\n',
    )
    crc_python = _write_fixture(
        authority_root / "crc_python", b"#!/bin/sh\nexit 70\n", 0o700
    )
    crc_worker = _write_fixture(
        authority_root / "crc_worker.py", b"raise SystemExit(70)\n", 0o700
    )
    cloudsdk = authority_root / "cloudsdk"
    cloudsdk.mkdir(mode=0o700)
    _write_fixture(
        cloudsdk / "application_default_credentials.json", b"{}\n"
    )
    authority = minimal.LiveAuthority(
        governing_commit=governing_commit,
        selection_authority_commit=governing_commit,
        legacy_session_sha256="6" * 64,
        legacy_session_size=1,
        legacy_session_repeated_assignment_count=0,
        legacy_session_repeated_name_count=0,
        legacy_session_conflict_count=0,
        checkpoint=checkpoint,
        checkpoint_sha256=core.sha256_file(checkpoint),
        environment_receipt=environment,
        environment_receipt_sha256=core.sha256_file(environment),
        gcloud=gcloud,
        gcloud_sha256=core.sha256_file(gcloud),
        gcloud_receipt=gcloud_receipt,
        gcloud_receipt_sha256=core.sha256_file(gcloud_receipt),
        cloudsdk_config=cloudsdk,
        crc32c_python=crc_python,
        crc32c_worker=crc_worker,
        crc32c_python_sha256=core.sha256_file(crc_python),
        crc32c_worker_sha256=core.sha256_file(crc_worker),
        crc32c_distribution_sha256="5" * 64,
        billing_variable="LVEF_C3_GCP_BILLING_PROJECT",
        billing_project="synthetic-private-project",
        selected_studies=selected_studies,
        selected_studies_sha256=core.sha256_file(selected_studies),
        selected_source=selected_source,
        selected_source_sha256=core.sha256_file(selected_source),
        source_metadata=source_metadata,
        split_map=split_map,
        split_map_sha256=core.sha256_file(split_map),
    )
    hashes = {
        "production_contract": core.sha256_file(minimal.CONTRACT_PATH),
        "source_metadata": core.sha256_file(source_metadata),
        "split_map": core.sha256_file(split_map),
        "checkpoint": core.sha256_file(checkpoint),
        "environment_receipt": core.sha256_file(environment),
        "state_machine_schema": core.sha256_file(minimal.STATE_MACHINE_PATH),
        "resume_ledger_schema": core.sha256_file(minimal.RESUME_LEDGER_PATH),
        "gcloud_resolution_receipt": core.sha256_file(gcloud_receipt),
        "gcloud_executable": core.sha256_file(gcloud),
        "crc32c_python_executable": core.sha256_file(crc_python),
        "crc32c_worker": core.sha256_file(crc_worker),
        "crc32c_distribution": "5" * 64,
        "governing_commit": hashlib.sha256(
            governing_commit.encode("ascii")
        ).hexdigest(),
        "selection_authority_commit": hashlib.sha256(
            governing_commit.encode("ascii")
        ).hexdigest(),
        "legacy_session_environment": authority.legacy_session_sha256,
        "selected_studies": core.sha256_file(selected_studies),
        "historical_study_manifest": core.sha256_file(historical_manifest),
        "prior_smoke_source_manifest": core.sha256_file(prior_smoke_manifest),
        "prior_smoke_source_summary": core.sha256_file(prior_smoke_summary),
        "prior_smoke_source_safety": core.sha256_file(prior_smoke_safety),
        "prior_smoke_preservation_manifest": core.sha256_file(
            prior_smoke_preservation
        ),
    }
    sealed = fixture.manifest_contract.build_sealed_manifest(
        selected_studies=selected,
        source_objects=objects,
        source_authority_commit=governing_commit,
        source_manifest_sha256=core.sha256_file(selected_source),
        source_configuration_hashes=hashes,
    )
    manifest_path = _write_fixture(
        authority_root / "exact_five_manifest.restricted.json",
        fixture.manifest_contract.serialize_manifest(sealed),
    )
    return authority, sealed, manifest_path, package_inventory


def _synthetic_body_transport(
    fixture: ModuleType, sealed: dict[str, Any], calls: dict[str, int]
) -> Any:
    payloads = {
        row["source_object_key"]: fixture._minimal_part10_dicom_bytes(
            int(Path(row["source_relative_path"]).stem.rsplit("_", 1)[1])
        )
        for row in fixture._manifest_objects(sealed)
    }

    class _Transport:
        def fetch(
            self,
            expectation: Any,
            *,
            partial_path: Path,
            billing_project: str,
            access_token: str,
        ) -> dict[str, Any]:
            assert billing_project == "synthetic-private-project"
            assert access_token == "synthetic-token"
            payload = payloads[expectation.source_object_key]
            partial_path.write_bytes(payload)
            calls["mock_requester_pays_transfer"] = calls.get(
                "mock_requester_pays_transfer", 0
            ) + 1
            return {
                "schema_version": 2,
                "status": "BODY_TRANSFER_COMPLETE_UNVERIFIED",
                "source_object_key": expectation.source_object_key,
                "size_bytes": expectation.size_bytes,
                "generation": expectation.generation,
                "md5_base64": expectation.md5_base64,
                "crc32c_base64": expectation.crc32c_base64,
                "media_request_count": 1,
                "object_body_bytes_read": expectation.size_bytes,
                "resume_offset_bytes": 0,
                "response_body_bytes_read": expectation.size_bytes,
                "final_partial_size_bytes": expectation.size_bytes,
                "content_range_validated": False,
            }

    return _Transport()


def _synthetic_token_provider(
    authority: minimal.LiveAuthority, calls: dict[str, int]
) -> Any:
    class _Provider:
        @staticmethod
        def validate_authority() -> dict[str, str]:
            return {
                "gcloud_resolution_receipt_sha256": core.sha256_file(
                    authority.gcloud_receipt
                ),
                "gcloud_executable_sha256": core.sha256_file(authority.gcloud),
            }

        @staticmethod
        def __call__() -> str:
            calls["mock_token_acquisition"] = calls.get(
                "mock_token_acquisition", 0
            ) + 1
            return "synthetic-token"

    return _Provider()


def test_direct_manifest_plan_rejects_each_runtime_identity_tamper(
    monkeypatch: Any,
) -> None:
    """Every sealed source/runtime identity must fail closed before effects."""

    fixture = _fixture_module()
    with tempfile.TemporaryDirectory(
        prefix="minimal-authority-tamper-", dir=str(minimal.SAFE_TEMPORARY_ROOT)
    ) as directory:
        root = Path(directory).resolve()
        authority, sealed, _, _ = _synthetic_authority_and_manifest(fixture, root)
        authority_root = authority.selected_studies.parent
        monkeypatch.setattr(
            core,
            "EXPECTED_SPLIT_MAP_SHA256",
            core.sha256_file(authority.split_map),
        )
        monkeypatch.setattr(
            core,
            "EXPECTED_CHECKPOINT_SHA256",
            core.sha256_file(authority.checkpoint),
        )
        monkeypatch.setattr(
            core,
            "EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256",
            core.sha256_file(authority.selected_source),
        )
        monkeypatch.setattr(
            minimal,
            "CURRENT_ENVIRONMENT_SHA256",
            core.sha256_file(authority.environment_receipt),
        )
        fixed_paths = {
            "HISTORICAL_STUDY_MANIFEST_PATH": authority_root
            / "historical_study_manifest.csv",
            "PRIOR_SMOKE_SOURCE_MANIFEST_PATH": authority_root
            / "prior_smoke_source_manifest.restricted.csv",
            "PRIOR_SMOKE_SOURCE_SUMMARY_PATH": authority_root
            / "prior_smoke_source_summary.json",
            "PRIOR_SMOKE_SOURCE_SAFETY_PATH": authority_root
            / "prior_smoke_source_safety.json",
            "PRIOR_SMOKE_PRESERVATION_MANIFEST_PATH": authority_root
            / "prior_smoke_preservation_manifest.tsv",
        }
        for name, path in fixed_paths.items():
            monkeypatch.setattr(minimal, name, path)
        monkeypatch.setattr(
            minimal,
            "HISTORICAL_STUDY_MANIFEST_SHA256",
            core.sha256_file(fixed_paths["HISTORICAL_STUDY_MANIFEST_PATH"]),
        )
        monkeypatch.setattr(
            minimal,
            "PRIOR_SMOKE_PRESERVATION_MANIFEST_SHA256",
            core.sha256_file(
                fixed_paths["PRIOR_SMOKE_PRESERVATION_MANIFEST_PATH"]
            ),
        )

        # The untampered fixture is accepted by the same production-plan path.
        minimal._build_direct_manifest_plan(sealed, authority=authority)
        cases = {
            "source_metadata": "MINIMAL_SELECTED_SOURCE_AUTHORITY_MISMATCH",
            "split_map": "MINIMAL_MANIFEST_RUNTIME_AUTHORITY_MISMATCH",
            "checkpoint": "MINIMAL_MANIFEST_RUNTIME_AUTHORITY_MISMATCH",
            "environment_receipt": "MINIMAL_MANIFEST_RUNTIME_AUTHORITY_MISMATCH",
            "gcloud_executable": "MINIMAL_MANIFEST_RUNTIME_AUTHORITY_MISMATCH",
            "crc32c_worker": "MINIMAL_MANIFEST_RUNTIME_AUTHORITY_MISMATCH",
        }
        for logical_name, expected_code in cases.items():
            tampered = copy.deepcopy(sealed)
            for row in tampered["manifest"]["source_configuration_hashes"]:
                if row["logical_name"] == logical_name:
                    row["sha256"] = "f" * 64
                    break
            else:
                raise AssertionError(f"missing authority binding {logical_name}")
            try:
                minimal._build_direct_manifest_plan(tampered, authority=authority)
            except minimal.MinimalCanaryError as exc:
                assert exc.code == expected_code
            else:
                raise AssertionError(f"accepted tampered authority {logical_name}")


def _synthetic_external_crc32c_worker(
    fixture: ModuleType,
    authority: minimal.LiveAuthority,
    calls: dict[str, int],
) -> type:
    """Test double for the closed external-worker seam, never production authority."""

    class _Worker:
        def __init__(
            self,
            *,
            python_executable: Path,
            worker_script: Path,
            expected_python_sha256: str,
            expected_worker_sha256: str,
            expected_distribution_sha256: str,
            allowed_root: Path = Path("/restricted/projectnb"),
        ) -> None:
            assert python_executable == authority.crc32c_python
            assert worker_script == authority.crc32c_worker
            assert expected_python_sha256 == core.sha256_file(
                authority.crc32c_python
            )
            assert expected_worker_sha256 == core.sha256_file(
                authority.crc32c_worker
            )
            assert expected_distribution_sha256 == (
                authority.crc32c_distribution_sha256
            )
            assert allowed_root == Path("/restricted/projectnb")
            calls["synthetic_external_crc32c_worker_started"] = 1

        def __enter__(self) -> _Worker:
            return self

        def __exit__(self, *_args: Any) -> bool:
            calls["synthetic_external_crc32c_worker_closed"] = 1
            return False

        def digest(
            self,
            path: Path,
            request_id: str,
            *,
            chunk_size: int = 8 * 1024 * 1024,
        ) -> dict[str, Any]:
            assert re.fullmatch(r"[0-9a-f]{64}", request_id)
            before = path.stat(follow_symlinks=False)
            payload = path.read_bytes()
            after = path.stat(follow_symlinks=False)
            assert (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) == (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
            calls["synthetic_external_crc32c_digests"] = (
                calls.get("synthetic_external_crc32c_digests", 0) + 1
            )
            return {
                "protocol_version": 1,
                "status": "PASS",
                "request_id": request_id,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "md5_base64": base64.b64encode(
                    hashlib.md5(payload, usedforsecurity=False).digest()
                ).decode("ascii"),
                "crc32c_base64": fixture._synthetic_crc32c_base64(payload),
                "file_device": int(after.st_dev),
                "file_inode": int(after.st_ino),
                "file_mtime_ns": int(after.st_mtime_ns),
                "chunk_size_bytes": chunk_size,
                "backend": "google_crc32c_c_external_worker_v1",
            }

    return _Worker


def test_minimal_canary_exact_five_end_to_end(monkeypatch: Any) -> None:
    """Exercise the deployed one-job builder with only external effects mocked."""

    fixture = _fixture_module()
    calls: dict[str, int] = {}
    with tempfile.TemporaryDirectory(
        prefix="minimal-canary-e2e-", dir=str(minimal.SAFE_TEMPORARY_ROOT)
    ) as directory:
        test_root = Path(directory).resolve()
        (
            authority,
            sealed,
            manifest_path,
            package_inventory,
        ) = _synthetic_authority_and_manifest(fixture, test_root)
        manifest_file_sha = core.sha256_file(manifest_path)
        attempt_id = minimal._minimal_run_identity(
            manifest_file_sha, authority.governing_commit
        )
        run_root = test_root / "minimal_canary_runs" / attempt_id

        def restricted_temp_path(path: Path, *, must_exist: bool = False) -> Path:
            candidate = Path(path)
            if (
                not candidate.is_absolute()
                or candidate.is_symlink()
                or not candidate.is_relative_to(test_root)
                or (must_exist and not candidate.exists())
            ):
                raise stages.ProductionStageError("SYNTHETIC_PATH_OUTSIDE_TEST_ROOT")
            return candidate

        checkpoint_bytes = authority.checkpoint.read_bytes()
        validated_contract = core.load_orchestration_contract(minimal.CONTRACT_PATH)
        monkeypatch.setattr(
            core,
            "EXPECTED_SPLIT_MAP_SHA256",
            core.sha256_file(authority.split_map),
        )
        monkeypatch.setattr(
            core,
            "EXPECTED_CHECKPOINT_SHA256",
            hashlib.sha256(checkpoint_bytes).hexdigest(),
        )
        monkeypatch.setattr(
            core,
            "EXPECTED_SELECTED_SOURCE_MANIFEST_SHA256",
            core.sha256_file(authority.selected_source),
        )
        monkeypatch.setattr(
            minimal,
            "CURRENT_ENVIRONMENT_SHA256",
            core.sha256_file(authority.environment_receipt),
        )
        monkeypatch.setattr(minimal, "PRODUCTION_ROOT", test_root)
        authority_root = authority.selected_studies.parent
        monkeypatch.setattr(
            minimal,
            "HISTORICAL_STUDY_MANIFEST_PATH",
            authority_root / "historical_study_manifest.csv",
        )
        monkeypatch.setattr(
            minimal,
            "HISTORICAL_STUDY_MANIFEST_SHA256",
            core.sha256_file(minimal.HISTORICAL_STUDY_MANIFEST_PATH),
        )
        monkeypatch.setattr(
            minimal,
            "PRIOR_SMOKE_SOURCE_MANIFEST_PATH",
            authority_root / "prior_smoke_source_manifest.restricted.csv",
        )
        monkeypatch.setattr(
            minimal,
            "PRIOR_SMOKE_SOURCE_SUMMARY_PATH",
            authority_root / "prior_smoke_source_summary.json",
        )
        monkeypatch.setattr(
            minimal,
            "PRIOR_SMOKE_SOURCE_SAFETY_PATH",
            authority_root / "prior_smoke_source_safety.json",
        )
        monkeypatch.setattr(
            minimal,
            "PRIOR_SMOKE_PRESERVATION_MANIFEST_PATH",
            authority_root / "prior_smoke_preservation_manifest.tsv",
        )
        monkeypatch.setattr(
            minimal,
            "PRIOR_SMOKE_PRESERVATION_MANIFEST_SHA256",
            core.sha256_file(minimal.PRIOR_SMOKE_PRESERVATION_MANIFEST_PATH),
        )
        monkeypatch.setattr(
            core,
            "load_orchestration_contract",
            lambda path: copy.deepcopy(validated_contract),
        )
        monkeypatch.setattr(stages, "require_projectnb_path", restricted_temp_path)
        monkeypatch.setattr(
            stages.importlib.metadata,
            "distributions",
            lambda: [
                SimpleNamespace(
                    metadata={"Name": row["name"]}, version=row["version"]
                )
                for row in package_inventory
            ],
        )
        monkeypatch.setattr(stages, "CHECKPOINT_FILENAME", authority.checkpoint.name)
        monkeypatch.setattr(stages, "CHECKPOINT_BYTES", len(checkpoint_bytes))
        monkeypatch.setattr(
            stages,
            "CHECKPOINT_SHA256",
            hashlib.sha256(checkpoint_bytes).hexdigest(),
        )
        original_subprocess_run = minimal.subprocess.run
        qsub_calls: list[list[str]] = []

        def no_scheduler_process(arguments: Any, *args: Any, **kwargs: Any) -> Any:
            command = [str(value) for value in arguments]
            if command and Path(command[0]).name == "qsub":
                qsub_calls.append(command)
                raise AssertionError("sequential job must never submit another job")
            return original_subprocess_run(arguments, *args, **kwargs)

        monkeypatch.setattr(minimal.subprocess, "run", no_scheduler_process)
        monkeypatch.setattr(
            core,
            "ExternalCRC32CDigestWorker",
            _synthetic_external_crc32c_worker(fixture, authority, calls),
        )
        modules = {
            "pydicom": fixture._synthetic_pydicom_module(),
            "cv2": fixture._synthetic_cv2_module(),
            **_mocked_torch_modules(fixture, calls),
        }
        dependencies = minimal.ProductionDependencies(
            token_provider_factory=lambda active: _synthetic_token_provider(
                active, calls
            ),
            transport_factory=lambda: _synthetic_body_transport(
                fixture, sealed, calls
            ),
            extraction_workers=1,
            echoprime_batch_size=8,
            monotonic_clock=lambda: 0.0,
            sleeper=lambda _seconds: (_ for _ in ()).throw(
                AssertionError("synthetic transfer must not retry")
            ),
        )
        claimed = minimal.claim_sealed_manifest_submission(
            manifest_path=manifest_path,
            authority=authority,
        )
        assert claimed["status"] == "PASS_MINIMAL_SUBMISSION_CLAIMED"
        try:
            minimal.claim_sealed_manifest_submission(
                manifest_path=manifest_path,
                authority=authority,
            )
        except minimal.MinimalCanaryError as exc:
            assert exc.code == "MINIMAL_OUTPUT_COLLISION"
        else:
            raise AssertionError("repeat claim must fail before scheduler submission")
        with fixture._installed_dependency_modules(modules):
            terminal = minimal.run_sealed_manifest(
                manifest_path=manifest_path,
                scheduler_job_identity="synthetic-minimal-job",
                authority=authority,
                run_root=run_root,
                dependencies=dependencies,
            )

        assert terminal["status"] == "PASS"
        assert terminal["completed_stage_count"] == 5
        assert terminal["scheduler_submission_count"] == 1
        assert qsub_calls == []
        assert calls == {
            "mock_requester_pays_transfer": 10,
            "mock_token_acquisition": 1,
            "mock_encoder_compute": 2,
            "synthetic_external_crc32c_worker_started": 1,
            "synthetic_external_crc32c_digests": 10,
            "synthetic_external_crc32c_worker_closed": 1,
        }
        ledger = core.load_strict_json(
            run_root / "minimal_canary_stage_ledger.restricted.json"
        )
        assert ledger["status"] == "PASS"
        assert tuple(ledger["completed_stages"]) == minimal.ORDERED_STAGES
        assert ledger["scheduler_job_identity"] == "synthetic-minimal-job"
        assert terminal["scheduler_job_identity"] == "synthetic-minimal-job"
        batch_root = run_root / "attempts" / attempt_id / "batches" / minimal.BATCH_ID
        with np.load(
            batch_root / "echoprime" / "study_embeddings.restricted.npz",
            allow_pickle=False,
        ) as archive:
            embeddings = archive["embeddings"]
        assert embeddings.shape == (5, 512)
        assert embeddings.dtype == np.float32
        assert np.isfinite(embeddings).all()
        summary = core.load_strict_json(
            run_root / "minimal_canary_finalization_receipt.aggregate_safe.json"
        )
        assert summary["status"] == (
            "PASS_CANARY_PRESERVATION_FINALIZED_RETAINED_CACHE"
        )
        assert summary["pooled_studies"] == 5
        assert summary["verified_source_objects"] == 10
        assert summary["dicom_readable_objects"] == 10
        assert summary["extracted_clips"] == 10
        assert summary["clip_embeddings"] == 10
        assert summary["raw_dicoms_retained"] is True
        assert summary["extracted_cache_retained"] is True
        assert summary["production_continuation_authorized"] is False


class _DependencyLightMonkeyPatch:
    def __init__(self) -> None:
        self._undo: list[tuple[Any, str, Any]] = []

    def setattr(self, target: Any, name: str, value: Any) -> None:
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self) -> None:
        for target, name, value in reversed(self._undo):
            setattr(target, name, value)


def main() -> int:
    patcher = _DependencyLightMonkeyPatch()
    try:
        test_minimal_canary_exact_five_end_to_end(patcher)
    finally:
        patcher.undo()
    print("LVEF_C3_MINIMAL_INTEGRATION=PASS")
    print("STUDIES_EXTRACTED=5")
    print("STUDY_EMBEDDINGS=5")
    print("SCHEDULER_SUBMISSION_COUNT=1")
    print("CLOUD_REQUESTS=0")
    print("QSUB_SUBMISSIONS=0")
    print("DICOM_BODIES_DOWNLOADED=NO")
    print("GPU_EXECUTION=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
