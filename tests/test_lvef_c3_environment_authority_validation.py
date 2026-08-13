from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: no credentials, identifiers, or production paths.

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_production_stages as stages
import capture_lvef_c3_production_environment as capture


def _expect_code(code: str, operation) -> None:
    try:
        operation()
    except stages.ProductionStageError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected {code}")


def _write_receipt(root: Path, receipt: dict[str, object]) -> tuple[Path, str]:
    path = root / "environment.restricted.json"
    path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path, stages.sha256_file(path)


def _packages() -> list[dict[str, str]]:
    return [
        {"name": "torch", "version": "2.11.0+cu130"},
    ]


def _runtime() -> dict[str, str]:
    return {
        "python_executable_sha256": capture.EXPECTED_PYTHON_SHA256,
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


def test_environment_receipt_producer_and_consumer_share_exact_closed_keys() -> None:
    assert capture.ENVIRONMENT_RECEIPT_KEYS == stages.ENVIRONMENT_RECEIPT_KEYS
    capture.validate_receipt_schema(_receipt())


def test_runtime_python_authority_resolves_virtual_environment_symlink() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "python-target"
        target.write_bytes(b"synthetic interpreter authority")
        link = root / "python"
        link.symlink_to(target)
        assert stages.resolved_python_executable_sha256(link) == hashlib.sha256(
            target.read_bytes()
        ).hexdigest()


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


def test_shared_environment_authority_accepts_equal_available_commit() -> None:
    receipt = _receipt()
    with tempfile.TemporaryDirectory() as directory:
        path, digest = _write_receipt(Path(directory), receipt)
        before_payload = path.read_bytes()
        before = path.stat(follow_symlinks=False)
        with mock.patch.object(
            stages,
            "validate_environment_receipt_against_current_runtime",
            return_value=receipt,
        ) as runtime, mock.patch.object(
            stages,
            "validate_crc32c_external_authority",
            return_value=receipt,
        ) as crc32c, mock.patch.object(
            stages.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as git:
            authority = stages.validate_environment_authority_for_scientific_commit(
                path,
                expected_environment_receipt_sha256=digest,
                scientific_governing_commit=str(receipt["governing_commit"]),
            )
        after = path.stat(follow_symlinks=False)
        assert path.read_bytes() == before_payload
        assert (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) == (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )

    assert authority == {
        "status": "ENVIRONMENT_AUTHORITY_COMMIT_EQUAL",
        "environment_receipt": receipt,
        "environment_receipt_sha256": digest,
        "environment_authority_commit": receipt["governing_commit"],
        "scientific_governing_commit": receipt["governing_commit"],
        "environment_authority_relation": "EQUAL",
    }
    runtime.assert_called_once_with(path)
    crc32c.assert_called_once_with(
        path,
        stages.PINNED_CRC32C_PYTHON,
        stages.CANONICAL_REPOSITORY_ROOT / "scripts/lvef_c3_crc32c_worker.py",
    )
    assert git.call_count == 2
    for called in git.call_args_list:
        assert called.args[0] == [
            "/usr/bin/git",
            "-C",
            str(stages.CANONICAL_REPOSITORY_ROOT),
            "cat-file",
            "-e",
            f'{receipt["governing_commit"]}^{{commit}}',
        ]


def test_shared_environment_authority_accepts_only_proven_strict_ancestor() -> None:
    receipt = _receipt()
    receipt["governing_commit"] = "a" * 40
    scientific_commit = "b" * 40
    with tempfile.TemporaryDirectory() as directory:
        path, digest = _write_receipt(Path(directory), receipt)
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch.object(
            stages,
            "validate_environment_receipt_against_current_runtime",
            return_value=receipt,
        ), mock.patch.object(
            stages,
            "validate_crc32c_external_authority",
            return_value=receipt,
        ), mock.patch.object(
            stages.subprocess, "run", return_value=completed
        ) as git:
            authority = stages.validate_environment_authority_for_scientific_commit(
                path,
                expected_environment_receipt_sha256=digest,
                scientific_governing_commit=scientific_commit,
            )

    assert authority["status"] == "ENVIRONMENT_AUTHORITY_COMMIT_ANCESTOR"
    assert authority["environment_authority_relation"] == "ANCESTOR"
    assert authority["environment_authority_commit"] == "a" * 40
    assert authority["scientific_governing_commit"] == scientific_commit
    assert git.call_count == 3
    arguments = git.call_args.args[0]
    assert arguments == [
        "/usr/bin/git",
        "-C",
        str(stages.CANONICAL_REPOSITORY_ROOT),
        "merge-base",
        "--is-ancestor",
        "a" * 40,
        scientific_commit,
    ]
    assert git.call_args.kwargs["check"] is False
    assert git.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert git.call_args.kwargs["stdout"] is subprocess.DEVNULL
    assert git.call_args.kwargs["stderr"] is subprocess.DEVNULL
    assert git.call_args.kwargs["env"] == {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }


def test_shared_environment_authority_rejects_nonancestor_and_git_failure() -> None:
    receipt = _receipt()
    receipt["governing_commit"] = "a" * 40
    with tempfile.TemporaryDirectory() as directory:
        path, digest = _write_receipt(Path(directory), receipt)
        for returncode, expected in (
            (1, "ENVIRONMENT_AUTHORITY_COMMIT_NOT_ANCESTOR"),
            (128, "ENVIRONMENT_AUTHORITY_ANCESTRY_UNAVAILABLE"),
        ):
            with mock.patch.object(
                stages,
                "validate_environment_receipt_against_current_runtime",
                return_value=receipt,
            ), mock.patch.object(
                stages,
                "validate_crc32c_external_authority",
                return_value=receipt,
            ), mock.patch.object(
                stages.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], returncode),
                ],
            ):
                _expect_code(
                    expected,
                    lambda: stages.validate_environment_authority_for_scientific_commit(
                        path,
                        expected_environment_receipt_sha256=digest,
                        scientific_governing_commit="b" * 40,
                    ),
                )
        with mock.patch.object(
            stages,
            "validate_environment_receipt_against_current_runtime",
            return_value=receipt,
        ), mock.patch.object(
            stages,
            "validate_crc32c_external_authority",
            return_value=receipt,
        ), mock.patch.object(
            stages.subprocess, "run", side_effect=OSError("synthetic unavailable")
        ):
            _expect_code(
                "ENVIRONMENT_AUTHORITY_ANCESTRY_UNAVAILABLE",
                lambda: stages.validate_environment_authority_for_scientific_commit(
                    path,
                    expected_environment_receipt_sha256=digest,
                    scientific_governing_commit="b" * 40,
                ),
            )


def test_shared_environment_authority_rejects_unavailable_equal_commit() -> None:
    receipt = _receipt()
    with tempfile.TemporaryDirectory() as directory:
        path, digest = _write_receipt(Path(directory), receipt)
        with mock.patch.object(
            stages,
            "validate_environment_receipt_against_current_runtime",
            return_value=receipt,
        ), mock.patch.object(
            stages,
            "validate_crc32c_external_authority",
            return_value=receipt,
        ), mock.patch.object(
            stages.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 128),
        ) as git:
            _expect_code(
                "ENVIRONMENT_AUTHORITY_ANCESTRY_UNAVAILABLE",
                lambda: stages.validate_environment_authority_for_scientific_commit(
                    path,
                    expected_environment_receipt_sha256=digest,
                    scientific_governing_commit=str(receipt["governing_commit"]),
                ),
            )
    assert git.call_count == 1
    assert git.call_args.args[0][-3:] == [
        "cat-file",
        "-e",
        f'{receipt["governing_commit"]}^{{commit}}',
    ]


def test_shared_environment_authority_rejects_descendant_and_sibling_commits() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = Path(directory).resolve()

        def git(*arguments: str) -> str:
            completed = subprocess.run(
                ["/usr/bin/git", "-C", str(repository), *arguments],
                check=True,
                capture_output=True,
                text=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "LC_ALL": "C",
                },
            )
            return completed.stdout.strip()

        git("init", "--quiet")
        tracked = repository / "authority.txt"
        tracked.write_text("base\n", encoding="utf-8")
        git("add", "authority.txt")
        git(
            "-c",
            "user.name=Synthetic Test",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "base",
        )
        base = git("rev-parse", "HEAD")
        tracked.write_text("base\nchild\n", encoding="utf-8")
        git("add", "authority.txt")
        git(
            "-c",
            "user.name=Synthetic Test",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "child",
        )
        child = git("rev-parse", "HEAD")
        git("checkout", "--quiet", "--detach", base)
        (repository / "sibling.txt").write_text("sibling\n", encoding="utf-8")
        git("add", "sibling.txt")
        git(
            "-c",
            "user.name=Synthetic Test",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "sibling",
        )
        sibling = git("rev-parse", "HEAD")

        ancestor_receipt = _receipt()
        ancestor_receipt["governing_commit"] = base
        ancestor_path, ancestor_digest = _write_receipt(
            repository, ancestor_receipt
        )
        with mock.patch.object(
            stages,
            "CANONICAL_REPOSITORY_ROOT",
            repository,
        ), mock.patch.object(
            stages,
            "validate_environment_receipt_against_current_runtime",
            return_value=ancestor_receipt,
        ), mock.patch.object(
            stages,
            "validate_crc32c_external_authority",
            return_value=ancestor_receipt,
        ):
            ancestor = stages.validate_environment_authority_for_scientific_commit(
                ancestor_path,
                expected_environment_receipt_sha256=ancestor_digest,
                scientific_governing_commit=child,
            )
        assert ancestor["status"] == "ENVIRONMENT_AUTHORITY_COMMIT_ANCESTOR"
        assert ancestor["environment_authority_relation"] == "ANCESTOR"

        for environment_commit, scientific_commit in (
            (child, base),
            (sibling, child),
        ):
            receipt = _receipt()
            receipt["governing_commit"] = environment_commit
            path, digest = _write_receipt(repository, receipt)
            with mock.patch.object(
                stages,
                "CANONICAL_REPOSITORY_ROOT",
                repository,
            ), mock.patch.object(
                stages,
                "validate_environment_receipt_against_current_runtime",
                return_value=receipt,
            ), mock.patch.object(
                stages,
                "validate_crc32c_external_authority",
                return_value=receipt,
            ):
                _expect_code(
                    "ENVIRONMENT_AUTHORITY_COMMIT_NOT_ANCESTOR",
                    lambda: stages.validate_environment_authority_for_scientific_commit(
                        path,
                        expected_environment_receipt_sha256=digest,
                        scientific_governing_commit=scientific_commit,
                    ),
                )


def test_shared_environment_authority_rejects_malformed_commit_and_hash_binding() -> None:
    receipt = _receipt()
    with tempfile.TemporaryDirectory() as directory:
        path, digest = _write_receipt(Path(directory), receipt)
        _expect_code(
            "ENVIRONMENT_AUTHORITY_COMMIT_INVALID",
            lambda: stages.validate_environment_authority_for_scientific_commit(
                path,
                expected_environment_receipt_sha256=digest,
                scientific_governing_commit="not-a-commit",
            ),
        )
        _expect_code(
            "ENVIRONMENT_RECEIPT_HASH_BINDING_MISMATCH",
            lambda: stages.validate_environment_authority_for_scientific_commit(
                path,
                expected_environment_receipt_sha256="not-a-hash",
                scientific_governing_commit=str(receipt["governing_commit"]),
            ),
        )


def test_shared_environment_authority_rejects_hash_before_runtime_or_git() -> None:
    receipt = _receipt()
    with tempfile.TemporaryDirectory() as directory:
        path, _ = _write_receipt(Path(directory), receipt)
        with mock.patch.object(
            stages, "validate_environment_receipt_against_current_runtime"
        ) as runtime, mock.patch.object(
            stages, "validate_crc32c_external_authority"
        ) as crc32c, mock.patch.object(stages.subprocess, "run") as git:
            _expect_code(
                "ENVIRONMENT_RECEIPT_HASH_BINDING_MISMATCH",
                lambda: stages.validate_environment_authority_for_scientific_commit(
                    path,
                    expected_environment_receipt_sha256="0" * 64,
                    scientific_governing_commit=str(receipt["governing_commit"]),
                ),
            )
    runtime.assert_not_called()
    crc32c.assert_not_called()
    git.assert_not_called()


def test_shared_environment_authority_rejects_receipt_tamper_during_validation() -> None:
    receipt = _receipt()
    with tempfile.TemporaryDirectory() as directory:
        path, digest = _write_receipt(Path(directory), receipt)

        def tamper(_path: Path) -> dict[str, object]:
            path.write_bytes(path.read_bytes() + b" ")
            return receipt

        with mock.patch.object(
            stages,
            "validate_environment_receipt_against_current_runtime",
            side_effect=tamper,
        ), mock.patch.object(
            stages,
            "validate_crc32c_external_authority",
            return_value=receipt,
        ), mock.patch.object(stages.subprocess, "run") as git:
            _expect_code(
                "ENVIRONMENT_RECEIPT_HASH_BINDING_MISMATCH",
                lambda: stages.validate_environment_authority_for_scientific_commit(
                    path,
                    expected_environment_receipt_sha256=digest,
                    scientific_governing_commit=str(receipt["governing_commit"]),
                ),
            )
        git.assert_not_called()


def test_shared_environment_authority_preserves_live_runtime_and_crc32c_gates() -> None:
    receipt = _receipt()
    with tempfile.TemporaryDirectory() as directory:
        path, digest = _write_receipt(Path(directory), receipt)
        with mock.patch.object(
            stages,
            "validate_environment_receipt_against_current_runtime",
            side_effect=stages.ProductionStageError(
                "RUNNING_ENVIRONMENT_RUNTIME_MISMATCH"
            ),
        ), mock.patch.object(
            stages, "validate_crc32c_external_authority"
        ) as crc32c, mock.patch.object(stages.subprocess, "run") as git:
            _expect_code(
                "ENVIRONMENT_RECEIPT_LIVE_RUNTIME_MISMATCH",
                lambda: stages.validate_environment_authority_for_scientific_commit(
                    path,
                    expected_environment_receipt_sha256=digest,
                    scientific_governing_commit=str(receipt["governing_commit"]),
                ),
            )
        crc32c.assert_not_called()
        git.assert_not_called()


def test_shared_environment_authority_normalizes_package_cuda_and_activity_failures() -> None:
    receipt = _receipt()
    with tempfile.TemporaryDirectory() as directory:
        path, digest = _write_receipt(Path(directory), receipt)
        for underlying in (
            "RUNNING_PACKAGE_INVENTORY_MISMATCH",
            "CUDA_CUDNN_RUNTIME_UNAVAILABLE",
            "ENVIRONMENT_RECEIPT_ACTIVITY_FLAG_INVALID",
        ):
            with mock.patch.object(
                stages,
                "validate_environment_receipt_against_current_runtime",
                side_effect=stages.ProductionStageError(underlying),
            ), mock.patch.object(
                stages, "validate_crc32c_external_authority"
            ) as crc32c, mock.patch.object(stages.subprocess, "run") as git:
                _expect_code(
                    "ENVIRONMENT_RECEIPT_LIVE_RUNTIME_MISMATCH",
                    lambda: stages.validate_environment_authority_for_scientific_commit(
                        path,
                        expected_environment_receipt_sha256=digest,
                        scientific_governing_commit=str(receipt["governing_commit"]),
                    ),
                )
            crc32c.assert_not_called()
            git.assert_not_called()

        with mock.patch.object(
            stages,
            "validate_environment_receipt_against_current_runtime",
            return_value=receipt,
        ), mock.patch.object(
            stages,
            "validate_crc32c_external_authority",
            side_effect=stages.ProductionStageError(
                "CRC32C_EXTERNAL_RUNTIME_AUTHORITY_MISMATCH"
            ),
        ), mock.patch.object(stages.subprocess, "run") as git:
            _expect_code(
                "ENVIRONMENT_RECEIPT_LIVE_RUNTIME_MISMATCH",
                lambda: stages.validate_environment_authority_for_scientific_commit(
                    path,
                    expected_environment_receipt_sha256=digest,
                    scientific_governing_commit=str(receipt["governing_commit"]),
                ),
            )
        git.assert_not_called()


def test_shared_environment_authority_preserves_activity_and_schema_gates() -> None:
    for mutation, expected in (
        (("cloud_request_performed", True), "ENVIRONMENT_RECEIPT_ACTIVITY_FLAG_INVALID"),
        (("crc32c_runtime_source", "ambient"), "CRC32C_AUXILIARY_AUTHORITY_INVALID"),
    ):
        receipt = _receipt()
        receipt[mutation[0]] = mutation[1]
        _expect_code(
            expected,
            lambda receipt=receipt: stages.validate_environment_receipt_payload(
                receipt,
                live_packages=_packages(),
                live_runtime=_runtime(),
            ),
        )


def test_preservation_uses_shared_environment_authority_without_duplicate_schema() -> None:
    source = (ROOT / "scripts/preserve_lvef_c3_production_batch.py").read_text(
        encoding="utf-8"
    )
    assert source.count(
        "production_stages.validate_environment_authority_for_scientific_commit("
    ) == 1
    assert "ENVIRONMENT_RECEIPT_KEYS" not in source
    assert 'environment.get("google_crc32c_implementation")' not in source
    assert 'environment.get("cloud_request_performed")' not in source
    assert "ENVIRONMENT_PROVENANCE_AUTHORITY_INVALID" in source
