from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: no credentials, identifiers, or production paths.

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_production_stages as stages


def _packages() -> list[dict[str, str]]:
    return [
        {"name": "torch", "version": "2.11.0+cu130"},
    ]


def _runtime() -> dict[str, str]:
    return {
        "python_executable_sha256": "a" * 64,
        "python_version": "3.10.12",
        "torch_version": "2.11.0+cu130",
        "torchvision_version": "0.26.0+cu130",
        "cuda_version": "13.0",
        "cudnn_version": "91002",
        "operating_system": "synthetic-linux",
    }


def _receipt() -> dict[str, object]:
    packages = _packages()
    return {
        "schema_version": 3,
        "artifact_type": "lvef_c3_production_environment_authority_v3",
        "status": "PASS_OFFLINE_RUNTIME_AUTHORITY_NO_GPU_EXECUTION",
        "governing_commit": "b" * 40,
        "captured_at_utc": "2026-08-10T12:00:00+00:00",
        "source_environment_receipt_sha256": "c" * 64,
        **_runtime(),
        "crc32c_runtime_source": "PINNED_CLOUDSDK_BUNDLED_PYTHON",
        "crc32c_python_executable_sha256": "d" * 64,
        "crc32c_python_version": "3.14.0",
        "crc32c_worker_sha256": "e" * 64,
        "crc32c_worker_protocol_version": 1,
        "google_crc32c_version": "1.8.0",
        "google_crc32c_implementation": "c",
        "google_crc32c_distribution_sha256": "f" * 64,
        "google_crc32c_distribution_file_count": 20,
        "google_crc32c_known_vector_base64": "4waSgw==",
        "package_inventory_sha256": hashlib.sha256(
            json.dumps(packages, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "package_count": len(packages),
        "package_inventory": packages,
        "gpu_execution_performed": False,
        "cloud_request_performed": False,
        "dicom_body_read": False,
        "model_fitted": False,
        "prediction_generated": False,
        "confirmatory_performance_accessed": False,
    }


def test_environment_receipt_retains_exact_closed_package_preimage() -> None:
    stages.validate_environment_receipt_payload(
        _receipt(), live_packages=_packages(), live_runtime=_runtime()
    )


def test_environment_receipt_rejects_tampered_package_preimage() -> None:
    receipt = _receipt()
    receipt["package_inventory"][0]["version"] = "changed"
    try:
        stages.validate_environment_receipt_payload(
            receipt, live_packages=_packages(), live_runtime=_runtime()
        )
    except stages.ProductionStageError as exc:
        assert str(exc) == "RUNNING_PACKAGE_INVENTORY_MISMATCH"
    else:
        raise AssertionError("Tampered package inventory preimage was accepted")


def test_environment_receipt_rejects_duplicate_normalized_package_names() -> None:
    receipt = _receipt()
    receipt["package_inventory"].append(
        {"name": "Torch", "version": "2.11.0+cu130"}
    )
    receipt["package_count"] = 2
    receipt["package_inventory_sha256"] = hashlib.sha256(
        json.dumps(
            receipt["package_inventory"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    try:
        stages.validate_environment_receipt_payload(
            receipt,
            live_packages=receipt["package_inventory"],
            live_runtime=_runtime(),
        )
    except stages.ProductionStageError as exc:
        assert str(exc) == "PACKAGE_INVENTORY_DUPLICATE_NAME"
    else:
        raise AssertionError("Duplicate normalized package name was accepted")


def test_environment_receipt_rejects_extra_key_and_changed_runtime() -> None:
    extra = _receipt()
    extra["credential"] = "synthetic-forbidden"
    changed = _receipt()
    changed["google_crc32c_implementation"] = "python"
    for receipt, expected in (
        (extra, "ENVIRONMENT_RECEIPT_SCHEMA_MISMATCH"),
        (changed, "CRC32C_AUXILIARY_AUTHORITY_INVALID"),
    ):
        try:
            stages.validate_environment_receipt_payload(
                receipt, live_packages=_packages(), live_runtime=_runtime()
            )
        except stages.ProductionStageError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError("Invalid environment receipt was accepted")


def test_every_production_stage_wrapper_revalidates_live_environment() -> None:
    source = (ROOT / "scripts" / "lvef_c3_production_stages.py").read_text(
        encoding="utf-8"
    )
    wrapper = source[
        source.index("def validate_wrapper_authority(") : source.index(
            "def validate_production_dicom_rows("
        )
    ]
    assert "validate_environment_receipt_against_current_runtime(" in wrapper
