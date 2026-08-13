from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_minimal_canary as minimal


def _expect(code: str, operation) -> None:
    try:
        operation()
    except minimal.MinimalCanaryError as exc:
        assert exc.code == code
    else:
        raise AssertionError(f"expected {code}")


def _write_private_environment(root: Path, name: str, payload: bytes) -> Path:
    path = (root / name).resolve()
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def test_legacy_session_projection_collapses_seven_identical_repeated_names() -> None:
    required_names = frozenset(f"REQUIRED_{index}" for index in range(7))
    expected = {
        name: f"literal_value_{index}"
        for index, name in enumerate(sorted(required_names))
    }
    payload = (
        "# synthetic legacy append history\n"
        + "".join(f"{name}={value}\n" for name, value in expected.items())
        + "".join(
            f"{name}={value}\n" for name, value in reversed(tuple(expected.items()))
        )
    ).encode("utf-8")
    with tempfile.TemporaryDirectory(
        dir=str(minimal.SAFE_TEMPORARY_ROOT)
    ) as directory:
        source = _write_private_environment(
            Path(directory), "legacy-session.env", payload
        )
        with mock.patch.object(minimal, "SESSION_AUTHORITY_PATH", source):
            projected = minimal._project_legacy_session_environment(
                required_names=required_names
            )

    assert dict(projected.values) == expected
    assert projected.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert projected.source_size == len(payload)
    assert projected.repeated_assignment_count == 7
    assert projected.repeated_name_count == 7
    assert projected.conflict_count == 0


def test_legacy_session_projection_rejects_conflicting_repeat_exactly() -> None:
    payload = b"REQUIRED_A=first\nREQUIRED_A=second\n"
    with tempfile.TemporaryDirectory(
        dir=str(minimal.SAFE_TEMPORARY_ROOT)
    ) as directory:
        source = _write_private_environment(
            Path(directory), "legacy-session.env", payload
        )
        with mock.patch.object(minimal, "SESSION_AUTHORITY_PATH", source):
            _expect(
                "MINIMAL_LEGACY_SESSION_DUPLICATE_CONFLICT",
                lambda: minimal._project_legacy_session_environment(
                    required_names=frozenset({"REQUIRED_A"})
                ),
            )


def test_legacy_session_projection_is_closed_for_malformed_missing_and_unknown() -> None:
    with tempfile.TemporaryDirectory(
        dir=str(minimal.SAFE_TEMPORARY_ROOT)
    ) as directory:
        root = Path(directory)
        malformed = _write_private_environment(
            root,
            "malformed.env",
            b"REQUIRED_A=valid_literal\n"
            b"REQUIRED_A=value with whitespace\n",
        )
        with mock.patch.object(minimal, "SESSION_AUTHORITY_PATH", malformed):
            _expect(
                "MINIMAL_PRIVATE_ENVIRONMENT_INVALID",
                lambda: minimal._project_legacy_session_environment(
                    required_names=frozenset({"REQUIRED_A"})
                ),
            )

        missing = _write_private_environment(
            root, "missing.env", b"REQUIRED_A=present\n"
        )
        with mock.patch.object(minimal, "SESSION_AUTHORITY_PATH", missing):
            _expect(
                "MINIMAL_PRIVATE_ENVIRONMENT_INCOMPLETE",
                lambda: minimal._project_legacy_session_environment(
                    required_names=frozenset({"REQUIRED_A", "REQUIRED_B"})
                ),
            )

        unknown = _write_private_environment(
            root,
            "unknown.env",
            b"UNKNOWN_NAME=ignored\nnot-an-assignment\nREQUIRED_A=closed_value\n",
        )
        with mock.patch.object(minimal, "SESSION_AUTHORITY_PATH", unknown):
            projected = minimal._project_legacy_session_environment(
                required_names=frozenset({"REQUIRED_A"})
            )
        assert dict(projected.values) == {"REQUIRED_A": "closed_value"}
        assert projected.repeated_assignment_count == 0
        assert projected.repeated_name_count == 0
        assert projected.conflict_count == 0

        with mock.patch.object(minimal, "SESSION_AUTHORITY_PATH", unknown):
            _expect(
                "MINIMAL_PRIVATE_ENVIRONMENT_INVALID",
                lambda: minimal._project_legacy_session_environment(
                    required_names=frozenset({"not_closed"})
                ),
            )


def test_canonical_literal_environment_still_rejects_identical_duplicates() -> None:
    payload = b"REQUIRED_A=identical\nREQUIRED_A=identical\n"
    with tempfile.TemporaryDirectory(
        dir=str(minimal.SAFE_TEMPORARY_ROOT)
    ) as directory:
        source = _write_private_environment(
            Path(directory), "canonical.env", payload
        )
        _expect(
            "MINIMAL_PRIVATE_ENVIRONMENT_INVALID",
            lambda: minimal._parse_literal_environment(
                source, required_names=frozenset({"REQUIRED_A"})
            ),
        )


def test_live_discovery_keeps_the_canonical_billing_environment_strict() -> None:
    with tempfile.TemporaryDirectory(
        dir=str(minimal.SAFE_TEMPORARY_ROOT)
    ) as directory:
        billing = _write_private_environment(
            Path(directory).resolve(),
            "billing.env",
            b"LVEF_C3_GCP_BILLING_PROJECT=synthetic-private-project\n"
            b"LVEF_C3_GCP_BILLING_PROJECT=synthetic-private-project\n",
        )
        values = {name: "literal" for name in minimal.LEGACY_SESSION_REQUIRED_NAMES}
        values["PREFLIGHT_ENV"] = str(billing)
        projection = minimal.LegacySessionProjection(
            values=values,
            source_sha256="a" * 64,
            source_size=1,
            repeated_assignment_count=7,
            repeated_name_count=7,
            conflict_count=0,
        )
        with mock.patch.object(
            minimal,
            "_project_legacy_session_environment",
            return_value=projection,
        ):
            _expect(
                "MINIMAL_PRIVATE_ENVIRONMENT_INVALID",
                minimal.discover_live_authority,
            )


def test_legacy_session_projection_never_executes_shell_text() -> None:
    with tempfile.TemporaryDirectory(
        dir=str(minimal.SAFE_TEMPORARY_ROOT)
    ) as directory:
        root = Path(directory)
        sentinel = root / "must-not-exist"
        payload = (
            "REQUIRED_A=/usr/bin/true\n"
            f"UNDECLARED=$(/usr/bin/touch {sentinel})\n"
        ).encode("utf-8")
        source = _write_private_environment(root, "literal-only.env", payload)
        forbidden = AssertionError("legacy projection attempted shell execution")
        with mock.patch.object(
            minimal, "SESSION_AUTHORITY_PATH", source
        ), mock.patch.object(
            minimal.subprocess, "run", side_effect=forbidden
        ) as run, mock.patch.object(
            minimal.subprocess, "Popen", side_effect=forbidden
        ) as popen, mock.patch.object(
            minimal.os, "system", side_effect=forbidden
        ) as system:
            projected = minimal._project_legacy_session_environment(
                required_names=frozenset({"REQUIRED_A"})
            )

        assert dict(projected.values) == {"REQUIRED_A": "/usr/bin/true"}
        assert not sentinel.exists()
        run.assert_not_called()
        popen.assert_not_called()
        system.assert_not_called()


def test_isolated_python_bootstrap_loads_only_the_tracked_sibling_root() -> None:
    script = ROOT / "scripts/lvef_c3_minimal_canary.py"
    probe = (
        "import runpy; "
        f"ns=runpy.run_path({str(script)!r}); "
        "assert tuple(ns['_production_functions']()) == ns['ORDERED_STAGES']"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_effective_private_mode_accepts_setgid_without_group_access() -> None:
    with tempfile.TemporaryDirectory(dir=str(minimal.SAFE_TEMPORARY_ROOT)) as directory:
        path = Path(directory) / "private"
        path.mkdir(mode=0o700)
        os.chmod(path, 0o2700)
        minimal.validate_private_directory(path, expected_group=path.stat().st_gid)
        os.chmod(path, 0o2770)
        _expect(
            "MINIMAL_PRIVATE_DIRECTORY_INVALID",
            lambda: minimal.validate_private_directory(path),
        )


def test_synthetic_preflight_is_one_job_and_five_ordered_stages() -> None:
    result = minimal.synthetic_preflight()
    assert result["status"] == "PASS_MINIMAL_EXACT_FIVE_SYNTHETIC_PREFLIGHT"
    assert result["scheduler_submission_count"] == 1
    assert tuple(result["ordered_stages"]) == minimal.ORDERED_STAGES
    assert result["cloud_requests"] == result["qsub_submissions"] == 0
    assert result["gpu_execution"] is False


def test_stage_failure_stops_successors_and_writes_terminal_fail() -> None:
    calls: list[str] = []
    manifest = minimal._synthetic_manifest()
    with tempfile.TemporaryDirectory(dir=str(minimal.SAFE_TEMPORARY_ROOT)) as directory:
        root = Path(directory) / "run"
        operations = {}
        for stage in minimal.ORDERED_STAGES:
            def operation(context, stage=stage):
                calls.append(stage)
                if stage == "DICOM_EXTRACTION":
                    raise minimal.MinimalCanaryError("SYNTHETIC_STAGE_FAILURE")
                return {"status": "PASS", "stage": stage}
            operations[stage] = operation
        _expect(
            "SYNTHETIC_STAGE_FAILURE",
            lambda: minimal.run_sequential_adapter(
                manifest=manifest, output_root=root, operations=operations
            ),
        )
        assert calls == ["DOWNLOAD", "DICOM_EXTRACTION"]
        terminal = json.loads(
            (root / "minimal_canary_terminal_receipt.aggregate_safe.json").read_text()
        )
        assert terminal["status"] == "FAIL"
        assert terminal["failed_stage"] == "DICOM_EXTRACTION"


def test_submitter_is_exactly_one_qsub_without_dag_or_array_flags() -> None:
    text = (ROOT / "scripts/scc_submit_lvef_c3_minimal_canary.sh").read_text()
    assert text.count('exec "$QSUB"') == 1
    assert "-r n" in text
    for forbidden in ("-hold_jid", "-t ", "-V", "-cwd"):
        assert forbidden not in text
    assert "scc_run_lvef_c3_canary.sh" not in text
    assert "--submit:1" in text and "--submit:2" not in text
    assert "$manifest" not in text
    assert text.index('"$RUNNER" --claim-sealed-manifest') < text.index(
        'exec "$QSUB"'
    )
    assert '"$RUNNER" --run-sealed-manifest' in text
    runner = (ROOT / "scripts/scc_run_lvef_c3_minimal_canary.sh").read_text()
    assert "test_lvef_c3_minimal_canary_integration.py" not in runner
    assert "--seal-exact-five-manifest:1" in runner
    assert "--seal-exact-five-manifest:2" not in runner
    assert runner.count('"$WORKTREE/scripts/lvef_c3_minimal_canary.py" "$@"') == 1
    assert "pip install" not in runner and "google-crc32c" not in runner
    assert "unset CUDA_VISIBLE_DEVICES" not in runner
    assert "export CUDA_VISIBLE_DEVICES=''" in runner
    assert runner.index('if [[ "$1" = --run-sealed-manifest ]]') < runner.index(
        "export CUDA_VISIBLE_DEVICES=''"
    )


def test_installation_reports_independent_runtime_markers() -> None:
    with mock.patch.object(
        minimal,
        "_validate_installation_files",
        return_value="a" * 40,
    ), mock.patch.object(
        minimal,
        "_validate_two_runtime_installation",
        return_value={
            "echoprime_runtime": "PASS",
            "crc32c_external_runtime": "PASS",
        },
    ):
        result = minimal.validate_installation(repository=ROOT)
    assert result["echoprime_runtime"] == "PASS"
    assert result["crc32c_external_runtime"] == "PASS"
    output = io.StringIO()
    with redirect_stdout(output):
        minimal._print_result(result)
    markers = output.getvalue().splitlines()
    assert "ECHOPRIME_RUNTIME=PASS" in markers
    assert "CRC32C_EXTERNAL_RUNTIME=PASS" in markers


def test_preseal_readiness_markers_do_not_claim_a_manifest_or_body_readiness() -> None:
    result = {
        "status": "PASS_MINIMAL_LIVE_AUTHORITY_NO_BODY_PREFLIGHT",
        "governing_commit": "a" * 40,
        "legacy_session_projection": "PASS",
        "legacy_session_repeated_names": 7,
        "legacy_session_conflicts": 0,
        "canonical_env_duplicate_rejection": "ENFORCED",
        "ready_to_seal_exact_five_manifest": "YES",
        "real_manifest_created": False,
        "ready_for_first_body_request": "NO",
        "cloud_requests": 0,
        "qsub_submissions": 0,
        "dicom_bodies_processed": 0,
        "gpu_execution": False,
    }
    output = io.StringIO()
    with redirect_stdout(output):
        minimal._print_result(result)
    markers = set(output.getvalue().splitlines())
    assert {
        "LEGACY_SESSION_PROJECTION=PASS",
        "LEGACY_SESSION_REPEATED_NAMES=7",
        "LEGACY_SESSION_CONFLICTS=0",
        "CANONICAL_ENV_DUPLICATE_REJECTION=ENFORCED",
        "READY_TO_SEAL_EXACT_FIVE_MANIFEST=YES",
        "REAL_CANARY_MANIFEST_CREATED=NO",
        "READY_FOR_FIRST_BODY_REQUEST=NO",
        "CLOUD_REQUESTS=0",
        "QSUB_SUBMISSIONS=0",
        "DICOM_BODIES_DOWNLOADED=NO",
        "GPU_EXECUTION=NO",
    }.issubset(markers)
    assert "REAL_CANARY_MANIFEST_CREATED=YES" not in markers
    assert "READY_FOR_FIRST_BODY_REQUEST=YES" not in markers

    _expect(
        "MINIMAL_RESULT_EFFECT_ATTESTATION_INCOMPLETE",
        lambda: minimal._print_result(
            {
                "status": "INCOMPLETE_SYNTHETIC_RESULT",
                "cloud_requests": 0,
                "qsub_submissions": 0,
                "gpu_execution": False,
            }
        ),
    )


def test_live_preflight_requires_a_ready_fixed_private_manifest_destination() -> None:
    authority = mock.Mock(
        governing_commit="a" * 40,
        legacy_session_repeated_name_count=7,
        legacy_session_conflict_count=0,
    )
    with tempfile.TemporaryDirectory(
        dir=str(minimal.SAFE_TEMPORARY_ROOT)
    ) as directory:
        root = Path(directory).resolve()
        owner_private = root / "owner_private"
        owner_private.mkdir(mode=0o700)
        output = owner_private / "exact_five_manifest.restricted.json"
        with mock.patch.object(
            minimal, "_validate_live_no_row_gates", return_value=authority
        ), mock.patch.object(minimal, "OWNER_PRIVATE_ROOT", owner_private), mock.patch.object(
            minimal, "EXACT_FIVE_MANIFEST_PATH", output
        ), mock.patch.object(
            minimal, "_synthetic_manifest", return_value={"manifest": {"studies": [1] * 5}}
        ):
            result = minimal.live_authority_no_body_preflight()
            assert result["ready_to_seal_exact_five_manifest"] == "YES"
            assert result["real_manifest_created"] is False

            output.write_bytes(b"existing-manifest-evidence\n")
            output.chmod(0o600)
            _expect(
                "MINIMAL_MANIFEST_OUTPUT_ALREADY_EXISTS",
                minimal.live_authority_no_body_preflight,
            )

        output.unlink()
        owner_private.chmod(0o770)
        with mock.patch.object(
            minimal, "_validate_live_no_row_gates", return_value=authority
        ), mock.patch.object(minimal, "OWNER_PRIVATE_ROOT", owner_private), mock.patch.object(
            minimal, "EXACT_FIVE_MANIFEST_PATH", output
        ):
            _expect(
                "MINIMAL_PRIVATE_DIRECTORY_INVALID",
                minimal.live_authority_no_body_preflight,
            )


def test_prepared_claim_is_no_clobber_and_adopted_once() -> None:
    manifest = minimal._synthetic_manifest()
    manifest_file_sha = "a" * 64
    governing_commit = "b" * 40
    calls: list[str] = []
    with tempfile.TemporaryDirectory(dir=str(minimal.SAFE_TEMPORARY_ROOT)) as directory:
        root = Path(directory) / minimal._minimal_run_identity(
            manifest_file_sha, governing_commit
        )
        root.mkdir(mode=0o700)
        minimal._atomic_json(
            root / "minimal_canary_stage_ledger.restricted.json",
            minimal._initial_ledger(
                manifest_sha256=manifest["manifest_sha256"],
                manifest_file_sha256=manifest_file_sha,
                governing_commit=governing_commit,
            ),
            replace=False,
        )

        def operation(stage: str):
            def run(_context):
                calls.append(stage)
                return {"status": "PASS", "stage": stage}
            return run

        terminal = minimal.run_sequential_adapter(
            manifest=manifest,
            output_root=root,
            operations={stage: operation(stage) for stage in minimal.ORDERED_STAGES},
            adopt_prepared=True,
            manifest_file_sha256=manifest_file_sha,
            governing_commit=governing_commit,
            scheduler_job_identity="12345",
        )
        assert terminal["status"] == "PASS"
        assert tuple(calls) == minimal.ORDERED_STAGES
        _expect(
            "MINIMAL_PREPARED_RUN_INVALID",
            lambda: minimal.run_sequential_adapter(
                manifest=manifest,
                output_root=root,
                operations={stage: operation(stage) for stage in minimal.ORDERED_STAGES},
                adopt_prepared=True,
                manifest_file_sha256=manifest_file_sha,
                governing_commit=governing_commit,
                scheduler_job_identity="12345",
            ),
        )


def test_minimal_path_has_no_packet_grant_or_attempt_state_import() -> None:
    text = (ROOT / "scripts/lvef_c3_minimal_canary.py").read_text()
    for forbidden in (
        "lvef_c3_canary_execution_authority",
        "lvef_c3_canary_dispatch",
        "lvef_c3_canary_state",
        "lvef_c3_execution_state",
        "import lvef_c3_canary_authority_materializer",
        "import lvef_c3_canary as ",
        "preparation_sequence",
        "stage_authorization",
        "preselection_authority",
        "lvef_c3_body_transfer_authorization_v2",
        "owner_authorization_recorded",
    ):
        assert forbidden not in text
    functions = minimal._production_functions()
    assert tuple(functions) == minimal.ORDERED_STAGES


def test_live_failure_markers_never_falsely_attest_zero_effects() -> None:
    live = minimal._failure_effect_markers(live_run=True)
    assert live == (
        "CLOUD_REQUESTS=NOT_ATTESTED",
        "QSUB_SUBMISSIONS=1",
        "DICOM_BODIES_DOWNLOADED=NOT_ATTESTED",
        "GPU_EXECUTION=NOT_ATTESTED",
    )
    assert minimal._failure_effect_markers(live_run=False) == (
        "CLOUD_REQUESTS=0",
        "QSUB_SUBMISSIONS=0",
        "DICOM_BODIES_DOWNLOADED=NO",
        "GPU_EXECUTION=NO",
    )
