from __future__ import annotations

# SYNTHETIC_TEST_DATA_ONLY: no cloud, credentials, identifiers, or DICOM data.

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_orchestration_core as core


WORKER = ROOT / "scripts" / "lvef_c3_crc32c_worker.py"
PYTHON = Path(sys.executable).resolve(strict=True)


def _probe() -> dict[str, object]:
    completed = subprocess.run(
        [str(PYTHON), "-I", str(WORKER), "--probe"],
        text=True,
        capture_output=True,
        check=False,
        env={
            "LC_ALL": "C",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "/synthetic/ambient/path/must/be/ignored",
        },
        cwd="/",
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def test_crc32c_probe_is_c_backed_isolated_and_distribution_bound() -> None:
    value = _probe()
    assert value["status"] == "PASS_CRC32C_AUXILIARY_RUNTIME"
    assert value["google_crc32c_implementation"] == "c"
    assert value["known_vector_crc32c_base64"] == "4waSgw=="
    assert value["cloud_requests"] == 0
    assert len(str(value["google_crc32c_distribution_sha256"])) == 64
    assert int(value["google_crc32c_distribution_file_count"]) > 0


def test_persistent_worker_hashes_multiple_files_in_one_process_without_paths() -> None:
    probe = _probe()
    with tempfile.TemporaryDirectory() as directory:
        allowed_root = Path(directory).resolve(strict=True)
        first = allowed_root / "first.bin"
        second = allowed_root / "second.bin"
        first_payload = b"first synthetic payload" * 200
        second_payload = b"second synthetic payload" * 300
        first.write_bytes(first_payload)
        second.write_bytes(second_payload)
        with core.ExternalCRC32CDigestWorker(
            python_executable=PYTHON,
            worker_script=WORKER,
            expected_python_sha256=core.sha256_file(PYTHON),
            expected_worker_sha256=core.sha256_file(WORKER),
            expected_distribution_sha256=str(
                probe["google_crc32c_distribution_sha256"]
            ),
            allowed_root=allowed_root,
        ) as worker:
            process_pid = worker._process.pid
            observed_first = worker.digest(first, "a" * 64, chunk_size=1024)
            observed_second = worker.digest(second, "b" * 64, chunk_size=1024)
            assert worker._process.pid == process_pid
        for observed, payload, path in (
            (observed_first, first_payload, first),
            (observed_second, second_payload, second),
        ):
            assert observed["sha256"] == hashlib.sha256(payload).hexdigest()
            assert observed["md5_base64"] == base64.b64encode(
                hashlib.md5(payload).digest()
            ).decode("ascii")
            assert observed["crc32c_base64"] == core._crc32c_base64(payload)
            assert str(path) not in json.dumps(observed)
            assert observed["backend"] == "google_crc32c_c_external_worker_v1"


def test_worker_rejects_symlink_outside_scope_and_wrong_distribution_authority() -> None:
    probe = _probe()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve(strict=True)
        outside = root.parent / f"{root.name}_outside.bin"
        outside.write_bytes(b"outside")
        target = root / "target.bin"
        target.write_bytes(b"target")
        linked = root / "linked.bin"
        linked.symlink_to(target)
        try:
            for path, expected in (
                (linked, "CRC32C_WORKER_DIGEST_PATH_SYMLINK_PROHIBITED"),
                (outside, "CRC32C_WORKER_DIGEST_PATH_OUTSIDE_PROJECTNB"),
            ):
                with core.ExternalCRC32CDigestWorker(
                    python_executable=PYTHON,
                    worker_script=WORKER,
                    expected_python_sha256=core.sha256_file(PYTHON),
                    expected_worker_sha256=core.sha256_file(WORKER),
                    expected_distribution_sha256=str(
                        probe["google_crc32c_distribution_sha256"]
                    ),
                    allowed_root=root,
                ) as worker:
                    try:
                        worker.digest(path, "c" * 64, chunk_size=1024)
                    except core.OrchestrationError as exc:
                        assert str(exc) == expected
                    else:
                        raise AssertionError("unsafe digest path was accepted")
        finally:
            outside.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        try:
            core.ExternalCRC32CDigestWorker(
                python_executable=PYTHON,
                worker_script=WORKER,
                expected_python_sha256=core.sha256_file(PYTHON),
                expected_worker_sha256=core.sha256_file(WORKER),
                expected_distribution_sha256="f" * 64,
                allowed_root=Path(directory).resolve(strict=True),
            )
        except core.OrchestrationError as exc:
            assert str(exc) == "CRC32C_WORKER_STARTUP_AUTHORITY_MISMATCH"
        else:
            raise AssertionError("changed distribution authority was accepted")


def test_external_worker_rejects_wrong_file_hashes_before_startup() -> None:
    expected_python = core.sha256_file(PYTHON)
    expected_worker = core.sha256_file(WORKER)
    for python_hash, worker_hash, expected in (
        ("0" * 64, expected_worker, "CRC32C_PYTHON_AUTHORITY_MISMATCH"),
        (expected_python, "0" * 64, "CRC32C_WORKER_AUTHORITY_MISMATCH"),
    ):
        try:
            core.ExternalCRC32CDigestWorker(
                python_executable=PYTHON,
                worker_script=WORKER,
                expected_python_sha256=python_hash,
                expected_worker_sha256=worker_hash,
                expected_distribution_sha256="1" * 64,
                allowed_root=Path(tempfile.gettempdir()).resolve(strict=True),
            )
        except core.OrchestrationError as exc:
            assert str(exc) == expected
        else:
            raise AssertionError("changed worker file authority was accepted")


def test_external_worker_rejects_non_c_backend_and_known_vector_mismatch() -> None:
    base_ready = {
        "protocol_version": 1,
        "status": "READY",
        "google_crc32c_version": "synthetic",
        "google_crc32c_implementation": "c",
        "google_crc32c_distribution_sha256": "1" * 64,
        "google_crc32c_distribution_file_count": 1,
        "known_vector_crc32c_base64": "4waSgw==",
    }
    for mutation in (
        {"google_crc32c_implementation": "python"},
        {"known_vector_crc32c_base64": "AAAAAA=="},
        {"google_crc32c_distribution_sha256": "2" * 64},
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(strict=True)
            worker = root / "synthetic_worker.py"
            ready = {**base_ready, **mutation}
            worker.write_text(
                "import json,sys\n"
                f"print(json.dumps({ready!r}, sort_keys=True), flush=True)\n"
                "for _line in sys.stdin: pass\n"
            )
            try:
                core.ExternalCRC32CDigestWorker(
                    python_executable=PYTHON,
                    worker_script=worker,
                    expected_python_sha256=core.sha256_file(PYTHON),
                    expected_worker_sha256=core.sha256_file(worker),
                    expected_distribution_sha256="1" * 64,
                    allowed_root=root,
                )
            except core.OrchestrationError as exc:
                assert str(exc) == "CRC32C_WORKER_STARTUP_AUTHORITY_MISMATCH"
            else:
                raise AssertionError("invalid external-worker READY was accepted")


def test_worker_protocol_fails_closed_on_duplicate_keys_and_oversized_line() -> None:
    with tempfile.TemporaryDirectory() as directory:
        allowed_root = Path(directory).resolve(strict=True)
        for payload in (
            '{"protocol_version":1,"protocol_version":1}\n',
            "{" + ("x" * 70_000) + "}\n",
        ):
            process = subprocess.Popen(
                [
                    str(PYTHON),
                    "-I",
                    "-u",
                    str(WORKER),
                    "--serve",
                    "--allowed-root",
                    str(allowed_root),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                cwd="/",
                env={"LC_ALL": "C", "PYTHONNOUSERSITE": "1"},
            )
            assert process.stdout is not None and process.stdin is not None
            ready = json.loads(process.stdout.readline())
            assert ready["status"] == "READY"
            process.stdin.write(payload)
            process.stdin.flush()
            response = json.loads(process.stdout.readline())
            assert response["status"] == "FAIL"
            assert "path" not in response
            assert process.wait(timeout=5) == 78
