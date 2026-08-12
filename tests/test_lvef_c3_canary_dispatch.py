from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lvef_c3_canary_dispatch as dispatch


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _authority(root: Path) -> dict:
    private = root / "private"
    private.mkdir(mode=0o700)
    runs = root / "canary_runs"
    runs.mkdir(mode=0o700)
    qsub = private / "qsub"
    worker = private / "worker.py"
    launcher = private / "launcher.sh"
    for path, body in ((qsub, b"qsub"), (worker, b"worker"), (launcher, b"launcher")):
        path.write_bytes(body)
        path.chmod(0o700)
    run_id = "lvef_c3_exact_five_canary_ab12cd34"
    return {
        "owner_authorized": True,
        "authorization_path": str(private / "execution_authorization_v1.json"),
        "authorization_sha256": "a" * 64,
        "authorization_file_sha256": "0" * 64,
        "launch_authority_sha256": "b" * 64,
        "run_id": run_id,
        "attempt_id": run_id,
        "output_root": str(runs / run_id),
        "manifest": {"file_sha256": "c" * 64, "embedded_sha256": "d" * 64},
        "scheduler_plan": {"canonical_sha256": "e" * 64},
        "scheduler": {
            "ordered_stage_ids": list(dispatch.ORDERED_STAGE_IDS),
            "scheduler_submission_count": 5,
            "maximum_scheduler_submission_count": 5,
            "gpu_stage_count": 1,
            "stage_retry_count": 0,
            "array_expansion_permitted": False,
            "automatic_resubmission_permitted": False,
            "production_continuation": False,
        },
        "qsub": {"path": str(qsub), "file_sha256": _sha(qsub)},
        "stage_worker": {"path": str(worker), "file_sha256": _sha(worker)},
        "stage_launcher": {"path": str(launcher), "file_sha256": _sha(launcher)},
    }


def _expect(code: str, operation) -> dispatch.CanaryDispatchError:
    try:
        operation()
    except dispatch.CanaryDispatchError as exc:
        assert exc.code == code, (exc.code, code)
        return exc
    raise AssertionError(f"expected {code}")


def test_durable_dispatch_submits_exact_five_once_and_binds_holds() -> None:
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(Path(directory))
        commands: list[tuple[str, ...]] = []

        def submit(command):
            commands.append(tuple(command))
            return str(7000 + len(commands))

        ledger = dispatch.dispatch_authorized_canary(authority, submitter=submit)
        assert ledger["status"] == "DISPATCHED_FROZEN_DAG"
        assert ledger["submission_count"] == 5
        assert ledger["production_continuation_triggered"] is False
        assert [row["stage_id"] for row in ledger["stages"]] == list(
            dispatch.ORDERED_STAGE_IDS
        )
        assert all(row["status"] == "SUBMITTED" for row in ledger["stages"])
        assert len(commands) == 5
        assert all(command[1:4] == ("-terse", "-r", "n") for command in commands)
        assert "-hold_jid" not in commands[0]
        for index, command in enumerate(commands[1:], start=1):
            hold = command.index("-hold_jid")
            assert command[hold + 1] == str(7000 + index)
        assert sum("gpus=1" in command for command in commands) == 1
        assert all(command[-2] == authority["authorization_path"] for command in commands)
        assert [command[-1] for command in commands] == list(dispatch.ORDERED_STAGE_IDS)
        snapshots = sorted(
            (Path(authority["output_root"]) / "scheduler_claims").glob("*.json")
        )
        assert len(snapshots) == 11
        _expect(
            "CANARY_DISPATCH_RUN_ROOT_COLLISION",
            lambda: dispatch.dispatch_authorized_canary(authority, submitter=submit),
        )
        assert len(commands) == 5


def test_submission_failure_is_terminal_and_blocks_all_later_qsubs() -> None:
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(Path(directory))
        commands: list[tuple[str, ...]] = []

        def submit(command):
            commands.append(tuple(command))
            if len(commands) == 3:
                raise RuntimeError("synthetic qsub failure")
            return str(8000 + len(commands))

        _expect(
            "CANARY_QSUB_SUBMISSION_FAILED",
            lambda: dispatch.dispatch_authorized_canary(authority, submitter=submit),
        )
        assert len(commands) == 3
        snapshots = sorted(
            (Path(authority["output_root"]) / "scheduler_claims").glob("*.json")
        )
        terminal = __import__("json").loads(snapshots[-1].read_text())
        assert terminal["status"] == "SUBMISSION_FAILED"
        assert terminal["failed_stage_id"] == "ECHOPRIME_EMBEDDING"
        assert terminal["submission_count"] == 2
        assert terminal["stages"][2]["status"] == "SUBMISSION_FAILED"
        assert all(row["status"] == "PENDING" for row in terminal["stages"][3:])


def test_dispatch_rejects_unbound_scope_dynamic_stage_and_tampered_binary() -> None:
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(Path(directory))
        authority["scheduler"]["scheduler_submission_count"] = 6
        _expect(
            "CANARY_DISPATCH_SCHEDULER_SCOPE_INVALID",
            lambda: dispatch.initialize_dispatch_ledger(authority),
        )
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(Path(directory))
        Path(authority["stage_launcher"]["path"]).write_text("changed")
        _expect(
            "CANARY_DISPATCH_EXECUTABLE_HASH_MISMATCH",
            lambda: dispatch.initialize_dispatch_ledger(authority),
        )
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(Path(directory))
        _expect(
            "CANARY_DISPATCH_UNDECLARED_STAGE",
            lambda: dispatch.build_qsub_command(
                authority, "MODEL_FITTING", predecessor_job_id=None,
                log_root=Path(directory), work_root=Path(directory),
            ),
        )
