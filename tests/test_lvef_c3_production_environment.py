from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock


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


def _probe() -> dict[str, object]:
    return {
        "protocol_version": 1,
        "status": "PASS_CRC32C_AUXILIARY_RUNTIME",
        "python_version": "3.14.0",
        "google_crc32c_version": "1.8.0",
        "google_crc32c_implementation": "c",
        "google_crc32c_distribution_sha256": "d" * 64,
        "google_crc32c_distribution_file_count": 20,
        "known_vector_crc32c_base64": "4waSgw==",
        "cloud_requests": 0,
    }


def _capture_fixture(root: Path):
    checkout = root / "checkout"
    (checkout / "scripts").mkdir(parents=True, exist_ok=True)
    worker = checkout / "scripts/lvef_c3_crc32c_worker.py"
    worker.write_text("# synthetic worker\n", encoding="utf-8")
    prior_path = root / "prior.json"
    prior_path.write_text(json.dumps(_prior()), encoding="utf-8")
    output = root / "environment.json"
    args = argparse.Namespace(
        prior_environment=prior_path,
        governing_commit="b" * 40,
        checkout_root=checkout,
        crc32c_python=Path(sys.executable),
        crc32c_python_sha256="c" * 64,
        crc32c_worker=worker,
        output=output,
    )
    torch = SimpleNamespace(
        __version__="2.11.0+cu130",
        version=SimpleNamespace(cuda="13.0"),
        backends=SimpleNamespace(
            cudnn=SimpleNamespace(version=lambda: 91002)
        ),
    )
    torchvision = SimpleNamespace(__version__="0.26.0+cu130")

    def import_module(name: str):
        return torch if name == "torch" else torchvision

    def write_temp(path: Path, value):
        temporary = path.with_name(".environment.synthetic-temp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        return temporary

    def promote(temporary: Path, destination: Path):
        os.link(temporary, destination)
        temporary.unlink()

    hooks = capture.CaptureHooks(
        import_module=import_module,
        find_optional_package=lambda name: None,
        interpreter_authority=lambda: (
            Path(sys.executable),
            capture.EXPECTED_PYTHON_SHA256,
        ),
        validate_checkout=lambda checkout, commit: None,
        load_prior=lambda path: {
            **_prior(),
            "python_version": platform.python_version(),
        },
        hash_file=lambda path: "a" * 64,
        inventory=lambda: [{"name": "torch", "version": "2.11.0+cu130"}],
        crc_probe=lambda *args, **kwargs: _probe(),
        write_temp=write_temp,
        promote=promote,
    )
    return args, hooks


def _capture_failure(root: Path, **hook_changes):
    args, hooks = _capture_fixture(root)
    hooks = replace(hooks, **hook_changes)
    try:
        capture.capture_environment(args, hooks=hooks)
    except capture.CaptureStageError as exc:
        return exc, args
    raise AssertionError("synthetic capture unexpectedly passed")


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
        root = Path(directory).resolve()
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


def test_environment_capture_success_promotes_one_closed_receipt() -> None:
    with tempfile.TemporaryDirectory() as directory:
        args, hooks = _capture_fixture(Path(directory))
        value = capture.capture_environment(args, hooks=hooks)
        assert args.output.is_file()
        assert set(value) == capture.ENVIRONMENT_RECEIPT_KEYS
        assert value["package_count"] == 1
        assert value["gpu_execution_performed"] is False
        assert not args.output.with_name(".environment.synthetic-temp").exists()


def test_environment_capture_classifies_mandatory_import_errors() -> None:
    for module_name, expected_stage in (
        ("torch", "TORCH_IMPORT"),
        ("torchvision", "TORCHVISION_IMPORT"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            args, hooks = _capture_fixture(Path(directory))

            def importer(name: str, failed: str = module_name):
                if name == failed:
                    raise ModuleNotFoundError("private path must not escape")
                return hooks.import_module(name)

            exc, failed_args = _capture_failure(
                Path(directory), import_module=importer
            )
            assert exc.stage == expected_stage
            assert exc.code == f"{expected_stage}_FAILED"
            assert exc.exception_class == "ModuleNotFoundError"
            assert not failed_args.output.exists()


def test_environment_capture_accepts_absent_documented_optional_package() -> None:
    with tempfile.TemporaryDirectory() as directory:
        args, hooks = _capture_fixture(Path(directory))
        hooks = replace(hooks, find_optional_package=lambda name: None)
        capture.capture_environment(args, hooks=hooks)
        assert args.output.exists()


def test_package_inventory_worker_classifies_nonzero_and_timeout() -> None:
    failed = subprocess.CompletedProcess(
        args=["synthetic"], returncode=7, stdout="", stderr=""
    )
    try:
        capture.package_inventory_subprocess(runner=lambda *a, **k: failed)
    except capture.EnvironmentAuthorityError as exc:
        assert str(exc) == "PACKAGE_INVENTORY_SUBPROCESS_FAILED"
    else:
        raise AssertionError("nonzero inventory worker was accepted")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic", 60)

    with tempfile.TemporaryDirectory() as directory:
        exc, args = _capture_failure(Path(directory), inventory=timeout)
        assert exc.stage == "PACKAGE_INVENTORY"
        assert exc.code == "PACKAGE_INVENTORY_TIMEOUT"
        assert exc.exception_class == "TimeoutExpired"
        assert not args.output.exists()


def test_package_inventory_worker_success_is_closed_and_sorted() -> None:
    packages = capture.package_inventory_subprocess()
    assert packages
    assert packages == sorted(
        packages, key=lambda row: (row["name"].casefold(), row["version"])
    )
    assert all(set(row) == {"name", "version"} for row in packages)


def test_environment_capture_classifies_inventory_worker_nonzero() -> None:
    with tempfile.TemporaryDirectory() as directory:

        def failed_inventory():
            raise capture.EnvironmentAuthorityError(
                "PACKAGE_INVENTORY_SUBPROCESS_FAILED"
            )

        exc, args = _capture_failure(
            Path(directory), inventory=failed_inventory
        )
        assert exc.stage == "PACKAGE_INVENTORY"
        assert exc.code == "PACKAGE_INVENTORY_SUBPROCESS_FAILED"
        assert not args.output.exists()


def test_environment_capture_rejects_missing_and_malformed_prior_receipt() -> None:
    for exception_class in (FileNotFoundError, ValueError):
        with tempfile.TemporaryDirectory() as directory:

            def broken(path: Path, kind=exception_class):
                raise kind("restricted detail")

            exc, args = _capture_failure(Path(directory), load_prior=broken)
            assert exc.stage == "PRIOR_RECEIPT_LOAD"
            assert exc.code == "PRIOR_RECEIPT_LOAD_FAILED"
            assert not args.output.exists()


def test_environment_capture_classifies_checkout_and_crc_failures() -> None:
    cases = (
        (
            "validate_checkout",
            lambda checkout, commit: (_ for _ in ()).throw(
                capture.EnvironmentAuthorityError("CHECKOUT_AUTHORITY_MISMATCH")
            ),
            "CHECKOUT_AUTHORITY",
            "CHECKOUT_AUTHORITY_MISMATCH",
        ),
        (
            "crc_probe",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("private")),
            "CRC32C_RUNTIME_PROBE",
            "CRC32C_RUNTIME_PROBE_FAILED",
        ),
    )
    for hook_name, operation, stage, code in cases:
        with tempfile.TemporaryDirectory() as directory:
            exc, args = _capture_failure(
                Path(directory), **{hook_name: operation}
            )
            assert (exc.stage, exc.code) == (stage, code)
            assert not args.output.exists()


def test_environment_capture_rejects_cuda_and_cudnn_unavailable_without_gpu() -> None:
    for cuda, cudnn, expected_stage, expected_code in (
        (None, 91002, "CUDA_METADATA", "CUDA_RUNTIME_UNAVAILABLE"),
        ("13.0", None, "CUDNN_METADATA", "CUDNN_RUNTIME_UNAVAILABLE"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            args, hooks = _capture_fixture(Path(directory))
            torch = SimpleNamespace(
                __version__="2.11.0+cu130",
                version=SimpleNamespace(cuda=cuda),
                backends=SimpleNamespace(
                    cudnn=SimpleNamespace(version=lambda result=cudnn: result)
                ),
            )
            importer = lambda name: torch if name == "torch" else hooks.import_module(name)
            exc, failed_args = _capture_failure(
                Path(directory), import_module=importer
            )
            assert (exc.stage, exc.code) == (expected_stage, expected_code)
            assert not failed_args.output.exists()


def test_environment_capture_classifies_cudnn_metadata_runtime_error() -> None:
    with tempfile.TemporaryDirectory() as directory:
        args, hooks = _capture_fixture(Path(directory))
        torch = SimpleNamespace(
            __version__="2.11.0+cu130",
            version=SimpleNamespace(cuda="13.0"),
            backends=SimpleNamespace(
                cudnn=SimpleNamespace(
                    version=lambda: (_ for _ in ()).throw(
                        RuntimeError("restricted runtime detail")
                    )
                )
            ),
        )
        importer = lambda name: torch if name == "torch" else hooks.import_module(name)
        exc, failed_args = _capture_failure(
            Path(directory), import_module=importer
        )
        assert exc.stage == "CUDNN_METADATA"
        assert exc.code == "CUDNN_METADATA_FAILED"
        assert exc.exception_class == "RuntimeError"
        assert not failed_args.output.exists()


def test_environment_capture_classifies_cuda_metadata_runtime_error() -> None:
    class BrokenVersion:
        @property
        def cuda(self):
            raise RuntimeError("restricted runtime detail")

    with tempfile.TemporaryDirectory() as directory:
        args, hooks = _capture_fixture(Path(directory))
        torch = SimpleNamespace(
            __version__="2.11.0+cu130",
            version=BrokenVersion(),
            backends=SimpleNamespace(cudnn=SimpleNamespace(version=lambda: 91002)),
        )
        importer = lambda name: torch if name == "torch" else hooks.import_module(name)
        exc, failed_args = _capture_failure(
            Path(directory), import_module=importer
        )
        assert exc.stage == "CUDA_METADATA"
        assert exc.code == "CUDA_METADATA_FAILED"
        assert exc.exception_class == "RuntimeError"
        assert not failed_args.output.exists()


def test_environment_capture_classifies_receipt_build_and_schema_failures() -> None:
    for hook_name, operation, expected_stage in (
        (
            "build",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("private")),
            "RECEIPT_BUILD",
        ),
        (
            "validate_receipt",
            lambda value: (_ for _ in ()).throw(ValueError("private")),
            "RECEIPT_SCHEMA_VALIDATION",
        ),
    ):
        with tempfile.TemporaryDirectory() as directory:
            exc, args = _capture_failure(
                Path(directory), **{hook_name: operation}
            )
            assert exc.stage == expected_stage
            assert exc.code == f"{expected_stage}_FAILED"
            assert not args.output.exists()


def test_environment_capture_classifies_temp_write_and_atomic_promotion() -> None:
    for hook_name, operation, expected_stage in (
        (
            "write_temp",
            lambda path, value: (_ for _ in ()).throw(OSError("private")),
            "RECEIPT_TEMP_WRITE",
        ),
        (
            "promote",
            lambda temporary, path: (_ for _ in ()).throw(OSError("private")),
            "RECEIPT_ATOMIC_PROMOTION",
        ),
    ):
        with tempfile.TemporaryDirectory() as directory:
            exc, args = _capture_failure(
                Path(directory), **{hook_name: operation}
            )
            assert exc.stage == expected_stage
            assert exc.code == f"{expected_stage}_FAILED"
            assert not args.output.exists()
            assert not (Path(directory) / ".environment.synthetic-temp").exists()


def test_environment_writer_is_atomic_no_clobber_and_owner_private() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        destination = root / "environment.json"
        with mock.patch.object(
            capture, "_validate_output_destination", lambda path: None
        ), mock.patch.object(
            capture, "require_no_symlink_ancestors", lambda *args, **kwargs: None
        ):
            temporary = capture.write_receipt_temp(destination, _receipt())
            assert temporary.exists() and not destination.exists()
            assert (temporary.stat().st_mode & 0o7777) == 0o600
            capture.promote_receipt_atomic(temporary, destination)
            assert destination.exists() and not temporary.exists()
            replacement = root / ".replacement"
            replacement.write_text("replacement", encoding="utf-8")
            replacement.chmod(0o600)
            try:
                capture.promote_receipt_atomic(replacement, destination)
            except capture.EnvironmentAuthorityError as exc:
                assert str(exc) == "OUTPUT_ALREADY_EXISTS"
            else:
                raise AssertionError("existing receipt was overwritten")
            assert json.loads(destination.read_text(encoding="utf-8"))
            assert replacement.read_text(encoding="utf-8") == "replacement"


def test_environment_capture_existing_destination_is_no_clobber() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        args, hooks = _capture_fixture(root)
        args.output.write_text("immutable predecessor\n", encoding="utf-8")
        exc, _ = _capture_failure(root, promote=hooks.promote)
        assert exc.stage == "RECEIPT_ATOMIC_PROMOTION"
        assert exc.exception_class == "FileExistsError"
        assert args.output.read_text(encoding="utf-8") == "immutable predecessor\n"


def test_every_upstream_failure_occurs_before_receipt_promotion() -> None:
    failures = (
        {"interpreter_authority": lambda: (_ for _ in ()).throw(RuntimeError())},
        {"import_module": lambda name: (_ for _ in ()).throw(ImportError())},
        {"find_optional_package": lambda name: (_ for _ in ()).throw(RuntimeError())},
        {"validate_checkout": lambda path, commit: (_ for _ in ()).throw(RuntimeError())},
        {"load_prior": lambda path: (_ for _ in ()).throw(ValueError())},
        {"inventory": lambda: (_ for _ in ()).throw(RuntimeError())},
        {"crc_probe": lambda *a, **k: (_ for _ in ()).throw(RuntimeError())},
        {"build": lambda **k: (_ for _ in ()).throw(RuntimeError())},
        {"validate_receipt": lambda value: (_ for _ in ()).throw(RuntimeError())},
        {"write_temp": lambda path, value: (_ for _ in ()).throw(OSError())},
    )
    for changes in failures:
        with tempfile.TemporaryDirectory() as directory:
            promoted: list[bool] = []
            args, hooks = _capture_fixture(Path(directory))
            hooks = replace(
                hooks,
                promote=lambda temporary, destination: promoted.append(True),
                **changes,
            )
            try:
                capture.capture_environment(args, hooks=hooks)
            except capture.CaptureStageError:
                pass
            else:
                raise AssertionError("upstream synthetic failure was accepted")
            assert promoted == []
            assert not args.output.exists()


def test_environment_capture_failure_payload_is_closed_and_sanitized() -> None:
    failure = capture.CaptureStageError(
        "TORCH_IMPORT", "TORCH_IMPORT_FAILED", "ModuleNotFoundError"
    )
    payload = capture.failure_payload(failure)
    assert set(payload) == {
        "status",
        "failure_stage",
        "stable_error_code",
        "exception_class",
    }
    serialized = json.dumps(payload)
    for forbidden in ("traceback", "message", "/restricted/", "credential"):
        assert forbidden not in serialized.casefold()


def test_environment_capture_unknown_internal_code_collapses_to_stage_code() -> None:
    try:
        capture._run_stage(
            "CHECKOUT_AUTHORITY",
            lambda: (_ for _ in ()).throw(
                capture.EnvironmentAuthorityError("UNREVIEWED_PRIVATE_FAILURE")
            ),
        )
    except capture.CaptureStageError as exc:
        assert exc.stage == "CHECKOUT_AUTHORITY"
        assert exc.code == "CHECKOUT_AUTHORITY_FAILED"
        assert exc.exception_class == "EnvironmentAuthorityError"
        assert "UNREVIEWED" not in json.dumps(capture.failure_payload(exc))
    else:
        raise AssertionError("unknown internal code escaped the closed taxonomy")


def test_environment_capture_main_emits_only_sanitized_failure() -> None:
    failure = capture.CaptureStageError(
        "TORCH_IMPORT", "TORCH_IMPORT_FAILED", "ModuleNotFoundError"
    )
    output = io.StringIO()
    with mock.patch.object(capture, "parse_args", return_value=object()):
        with mock.patch.object(capture, "capture_environment", side_effect=failure):
            with redirect_stdout(output):
                assert capture.main(["synthetic"]) == 2
    payload = json.loads(output.getvalue())
    assert payload == capture.failure_payload(failure)
    assert "private" not in output.getvalue().casefold()


def test_environment_capture_stage_set_and_routes_are_closed() -> None:
    assert capture.CAPTURE_STAGES == (
        "INTERPRETER_AUTHORITY",
        "TORCH_IMPORT",
        "TORCHVISION_IMPORT",
        "OPTIONAL_PACKAGE_IMPORTS",
        "CHECKOUT_AUTHORITY",
        "PRIOR_RECEIPT_LOAD",
        "PACKAGE_INVENTORY",
        "CRC32C_RUNTIME_PROBE",
        "CUDA_METADATA",
        "CUDNN_METADATA",
        "RECEIPT_BUILD",
        "RECEIPT_SCHEMA_VALIDATION",
        "RECEIPT_TEMP_WRITE",
        "RECEIPT_ATOMIC_PROMOTION",
    )
    source = (ROOT / "scripts/capture_lvef_c3_production_environment.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "gcloud ",
        "gsutil",
        "qsub ",
        "objects.list",
        "alt=media",
        "torch.cuda.is_available",
        "torch.tensor(",
    ):
        assert forbidden not in source


def test_environment_writer_cleans_temporary_file_after_fsync_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        destination = root / "environment.json"
        with mock.patch.object(
            capture, "_validate_output_destination", lambda path: None
        ), mock.patch.object(
            capture, "require_no_symlink_ancestors", lambda *args, **kwargs: None
        ), mock.patch.object(
            capture.os, "fsync", side_effect=OSError("synthetic fsync failure")
        ):
            try:
                capture.write_receipt_temp(destination, _receipt())
            except OSError:
                pass
            else:
                raise AssertionError("failed receipt fsync was accepted")
        assert not destination.exists()
        assert list(root.iterdir()) == []


def test_environment_writer_does_not_remove_colliding_temporary_file() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        destination = root / "environment.json"
        fixed_uuid = SimpleNamespace(hex="fixed")
        collision = root / ".environment.json.tmp-fixed"
        collision.write_text("immutable collision\n", encoding="utf-8")
        collision.chmod(0o600)
        with mock.patch.object(
            capture, "_validate_output_destination", lambda path: None
        ), mock.patch.object(
            capture, "require_no_symlink_ancestors", lambda *args, **kwargs: None
        ), mock.patch.object(capture.uuid, "uuid4", return_value=fixed_uuid):
            try:
                capture.write_receipt_temp(destination, _receipt())
            except FileExistsError:
                pass
            else:
                raise AssertionError("temporary-file collision was accepted")
        assert collision.read_text(encoding="utf-8") == "immutable collision\n"
        assert not destination.exists()


def test_environment_promotion_rolls_back_final_link_after_fsync_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        temporary = root / ".environment.temp"
        destination = root / "environment.json"
        temporary.write_text("{}\n", encoding="utf-8")
        temporary.chmod(0o600)
        with mock.patch.object(
            capture, "_validate_output_destination", lambda path: None
        ), mock.patch.object(
            capture, "require_no_symlink_ancestors", lambda *args, **kwargs: None
        ), mock.patch.object(
            capture.os, "fsync", side_effect=OSError("synthetic fsync failure")
        ):
            try:
                capture.promote_receipt_atomic(temporary, destination)
            except OSError:
                pass
            else:
                raise AssertionError("failed promotion fsync was accepted")
        assert temporary.exists()
        assert not destination.exists()


def test_environment_receipt_schema_rejects_hash_timestamp_and_crc_drift() -> None:
    for key, replacement in (
        ("package_inventory_sha256", "0" * 64),
        ("captured_at_utc", "2026-08-10T00:00:00"),
        ("google_crc32c_implementation", "python"),
    ):
        value = dict(_receipt())
        value[key] = replacement
        try:
            capture.validate_receipt_schema(value)
        except capture.EnvironmentAuthorityError as exc:
            assert str(exc) == "ENVIRONMENT_RECEIPT_SCHEMA_INVALID"
        else:
            raise AssertionError(f"schema drift was accepted for {key}")
