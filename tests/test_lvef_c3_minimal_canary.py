from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


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
    assert text.index('--claim-sealed-manifest "$manifest"') < text.index('exec "$QSUB"')
    runner = (ROOT / "scripts/scc_run_lvef_c3_minimal_canary.sh").read_text()
    assert "unset CUDA_VISIBLE_DEVICES" not in runner
    assert "export CUDA_VISIBLE_DEVICES=''" in runner
    assert runner.index('if [[ "$1" = --run-sealed-manifest ]]') < runner.index(
        "export CUDA_VISIBLE_DEVICES=''"
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
