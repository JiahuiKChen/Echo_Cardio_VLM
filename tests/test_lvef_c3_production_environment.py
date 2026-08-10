from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_lvef_c3_production_environment as capture


def _prior() -> dict[str, str]:
    return {
        "python_version": "3.10.12",
        "torch_version": "2.11.0+cu130",
        "torchvision_version": "0.26.0+cu130",
        "cuda_version": "13.0",
        "cudnn_version": "91002",
    }


def _receipt(implementation: str = "c"):
    probe = {
        "protocol_version": 1,
        "status": "PASS_CRC32C_AUXILIARY_RUNTIME",
        "python_version": "3.14.0",
        "google_crc32c_version": "1.8.0",
        "google_crc32c_implementation": implementation,
        "google_crc32c_distribution_sha256": "d" * 64,
        "google_crc32c_distribution_file_count": 20,
        "known_vector_crc32c_base64": "4waSgw==",
        "cloud_requests": 0,
    }
    return capture.build_receipt(
        prior=_prior(),
        prior_sha256="a" * 64,
        governing_commit="b" * 40,
        python_executable_sha256=capture.EXPECTED_PYTHON_SHA256,
        python_version="3.10.12",
        torch_version="2.11.0+cu130",
        torchvision_version="0.26.0+cu130",
        cuda_version="13.0",
        cudnn_version="91002",
        crc32c_python_executable_sha256="c" * 64,
        crc32c_worker_sha256="e" * 64,
        crc32c_probe=probe,
        packages=[{"name": "torch", "version": "2.11.0+cu130"}],
        captured_at_utc="2026-08-10T00:00:00+00:00",
    )


def test_production_environment_receipt_is_safe_and_c_backend_bound() -> None:
    value = _receipt()
    assert value["google_crc32c_implementation"] == "c"
    assert value["package_count"] == len(value["package_inventory"]) == 1
    assert value["gpu_execution_performed"] is False
    assert value["cloud_request_performed"] is False
    assert value["model_fitted"] is False
    serialized = json.dumps(value)
    assert "project" not in serialized.casefold()
    assert "credential" not in serialized.casefold()


def test_production_environment_rejects_python_or_crc_authority_change() -> None:
    for implementation, python_hash, expected in (
        (
            "python",
            capture.EXPECTED_PYTHON_SHA256,
            "GOOGLE_CRC32C_AUXILIARY_AUTHORITY_INVALID",
        ),
        ("c", "f" * 64, "PYTHON_AUTHORITY_CHANGED"),
    ):
        try:
            capture.build_receipt(
                prior=_prior(),
                prior_sha256="a" * 64,
                governing_commit="b" * 40,
                python_executable_sha256=python_hash,
                python_version="3.10.12",
                torch_version="2.11.0+cu130",
                torchvision_version="0.26.0+cu130",
                cuda_version="13.0",
                cudnn_version="91002",
                crc32c_python_executable_sha256="c" * 64,
                crc32c_worker_sha256="e" * 64,
                crc32c_probe={
                    "protocol_version": 1,
                    "status": "PASS_CRC32C_AUXILIARY_RUNTIME",
                    "python_version": "3.14.0",
                    "google_crc32c_version": "1.8.0",
                    "google_crc32c_implementation": implementation,
                    "google_crc32c_distribution_sha256": "d" * 64,
                    "google_crc32c_distribution_file_count": 20,
                    "known_vector_crc32c_base64": "4waSgw==",
                    "cloud_requests": 0,
                },
                packages=[{"name": "torch", "version": "2.11.0+cu130"}],
                captured_at_utc="2026-08-10T00:00:00+00:00",
            )
        except capture.EnvironmentAuthorityError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError("changed runtime authority was accepted")


def test_production_environment_output_is_no_clobber_and_projectnb_only() -> None:
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "environment.json"
        try:
            capture.write_no_clobber(target, _receipt())
        except capture.EnvironmentAuthorityError as exc:
            assert str(exc) == "OUTPUT_OUTSIDE_PROJECTNB"
        else:
            raise AssertionError("non-projectnb output was accepted")


def test_production_environment_rejects_symlink_in_any_authority_ancestor() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        real = root / "real"
        real.mkdir()
        linked = root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        target = linked / "environment.json"
        target.write_text("{}\n", encoding="utf-8")
        try:
            capture.require_no_symlink_ancestors(
                target, code="SYNTHETIC_AUTHORITY"
            )
        except capture.EnvironmentAuthorityError as exc:
            assert str(exc) == "SYNTHETIC_AUTHORITY_SYMLINK_COMPONENT"
        else:
            raise AssertionError("symlinked authority ancestor was accepted")


def test_package_inventory_rejects_duplicates_and_unsorted_rows() -> None:
    for rows, expected in (
        (
            [
                {"name": "alpha-crc", "version": "1"},
                {"name": "Alpha_CRC", "version": "2"},
            ],
            "PACKAGE_INVENTORY_DUPLICATE_NAME",
        ),
        (
            [
                {"name": "zeta", "version": "1"},
                {"name": "alpha", "version": "1"},
            ],
            "PACKAGE_INVENTORY_NOT_SORTED",
        ),
    ):
        try:
            capture.validate_package_inventory(rows)
        except capture.EnvironmentAuthorityError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError("ambiguous package inventory was accepted")
