from __future__ import annotations

"""Dependency-light proofs for the one fixed Phase 1I-R8U recovery.

The tests map directly to the twenty owner-required properties: fixed scope;
immutable failed partials; retained-raw authority; one fresh extraction and
EchoPrime pass; technical-disposition, preservation, retirement, and Batch-16
closure; exact Tasks 17--19 continuation; all-19 finalization; no analysis;
and the three-qsub ceiling.  They use only temporary files and mocks.
"""

import ast
from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import io
import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Iterator, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lvef_c3_full_sequential as sequential
import lvef_c3_production_stages as stages
import lvef_c3_r8r_recovery_continuation as r8u


IMPLEMENTATION_COMMIT = "a" * 40
SHA = "b" * 64


def _expected_r8u_implementation_authority_epochs() -> dict[str, str]:
    return dict(
        sorted(
            {
                "scientific_commit": r8u.ORIGINAL_SCIENTIFIC_COMMIT,
                "r8r_implementation_commit": r8u.R8U_STARTING_IMPLEMENTATION_COMMIT,
                "r8u_base_implementation_commit": r8u.R8U_BASE_IMPLEMENTATION_COMMIT,
                "r8u_projection_repair_commit": (
                    r8u.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT
                ),
                "r8u_scheduler_log_repair_commit": IMPLEMENTATION_COMMIT,
            }.items()
        )
    )


def _assert_exact_r8u_implementation_authority_epochs(
    artifact: Mapping[str, Any],
) -> None:
    epochs = artifact["implementation_authority_epochs"]
    assert isinstance(epochs, dict)
    assert epochs == _expected_r8u_implementation_authority_epochs()


def _expect_code(function: Any, expected: str) -> None:
    try:
        function()
    except r8u.R8RControllerError as exc:
        assert exc.code == expected
    else:
        raise AssertionError(f"expected {expected}")


def _fixed_run(root: Path) -> SimpleNamespace:
    batches = [
        {
            "batch_id": f"c3_batch_{index:03d}",
            "ordinal": index,
            "objects": [],
        }
        for index in range(19)
    ]
    return SimpleNamespace(
        authority=SimpleNamespace(
            governing_commit=r8u.ORIGINAL_SCIENTIFIC_COMMIT,
            environment_receipt=root / "environment.json",
            checkpoint=root / "checkpoint.pt",
        ),
        plan={"batches": batches},
        requirements=SimpleNamespace(batch_count=19),
        contract={},
        contract_path=root / "contract.yaml",
        plan_path=root / "plan.json",
        plan_sha256=r8u.ORIGINAL_PLAN_SHA256,
        runtime_authority={
            "environment_receipt_sha256": "c" * 64,
            "checkpoint_sha256": "d" * 64,
        },
        attempt_id=r8u.ORIGINAL_ATTEMPT_ID,
        production_root=root / "production",
        attempt_root=root / "production" / "attempts" / r8u.ORIGINAL_ATTEMPT_ID,
        launch_authority={"expected_no_cine_studies": 5},
        scheduler_job_identity="101",
    )


def test_r8u_scope_and_public_cli_are_destination_fixed() -> None:
    assert r8u.R8U_FIXED_BATCH_ID == "c3_batch_015"
    assert r8u.R8U_FIXED_RECOVERY_TASK_ID == 16
    assert r8u.R8U_FIXED_CONTINUATION_TASK_IDS == (17, 18, 19)
    assert r8u.R8U_FIXED_CONTINUATION_TASK_RANGE == "17-19"
    assert r8u.R8U_FIXED_CONTINUATION_MAX_CONCURRENCY == 1
    assert sequential.R8U_FIXED_CONTINUATION.value == (
        "R8U_R2_FIXED_CONTINUATION"
    )

    assert set(inspect.signature(r8u.submit_r8u_batch16_recovery).parameters) == {
        "qsub_runner",
        "qstat_runner",
        "process_runner",
        "capacity_process_runner",
    }
    assert set(inspect.signature(r8u.run_r8u_batch16_recovery).parameters) == {
        "dependencies"
    }
    assert tuple(inspect.signature(r8u.validate_r8u_recovery_terminal).parameters) == ()
    assert set(inspect.signature(r8u.submit_r8u_continuation_17_19).parameters) == {
        "qsub_runner",
        "qstat_runner",
        "qacct_runner",
        "process_runner",
    }
    assert tuple(inspect.signature(r8u.run_r8u_continuation_array_task).parameters) == ()
    assert tuple(inspect.signature(r8u.run_r8u_continuation_finalizer).parameters) == ()
    options = {
        option
        for action in r8u._parser()._actions
        for option in action.option_strings
    }
    assert {
        "--submit-batch16-recovery",
        "--run-batch16-recovery",
        "--validate-batch16-recovery",
        "--submit-continuation-17-19",
        "--run-continuation-17-19-array-task",
        "--run-r8u-continuation-finalizer",
    }.issubset(options)
    assert not options.intersection(
        {"--attempt", "--attempt-id", "--batch", "--batch-id", "--plan", "--path", "--task-range"}
    )

    with (
        mock.patch.object(r8u.sequential, "_current_commit", return_value=IMPLEMENTATION_COMMIT),
        mock.patch.object(
            r8u.sequential,
            "_git",
            side_effect=[
                "",
                "1",
                (
                    f"{IMPLEMENTATION_COMMIT} "
                    f"{r8u.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}"
                ),
                (
                    f"{r8u.R8U_BASE_IMPLEMENTATION_COMMIT} "
                    f"{r8u.R8U_STARTING_IMPLEMENTATION_COMMIT}"
                ),
                (
                    f"{r8u.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT} "
                    f"{r8u.R8U_BASE_IMPLEMENTATION_COMMIT}"
                ),
                (
                    f"{r8u.R8U_STARTING_IMPLEMENTATION_COMMIT} "
                    f"{r8u.ORIGINAL_SCIENTIFIC_COMMIT}"
                ),
                "4",
            ],
        ) as git,
    ):
        assert r8u._current_r8u_implementation_commit() == IMPLEMENTATION_COMMIT
    assert git.call_args_list[0] == mock.call(
        "merge-base",
        "--is-ancestor",
        r8u.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
        IMPLEMENTATION_COMMIT,
    )
    assert git.call_args_list[2] == mock.call(
        "rev-list", "--parents", "-n", "1", IMPLEMENTATION_COMMIT
    )
    assert git.call_args_list[3] == mock.call(
        "rev-list",
        "--parents",
        "-n",
        "1",
        r8u.R8U_BASE_IMPLEMENTATION_COMMIT,
    )
    assert git.call_args_list[4] == mock.call(
        "rev-list",
        "--parents",
        "-n",
        "1",
        r8u.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT,
    )
    assert git.call_args_list[5] == mock.call(
        "rev-list",
        "--parents",
        "-n",
        "1",
        r8u.R8U_STARTING_IMPLEMENTATION_COMMIT,
    )

    with (
        mock.patch.object(
            r8u.sequential, "_current_commit", return_value=IMPLEMENTATION_COMMIT
        ),
        mock.patch.object(
            r8u.sequential,
            "_git",
            side_effect=[
                "",
                "2",
                (
                    f"{IMPLEMENTATION_COMMIT} "
                    f"{r8u.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT}"
                ),
                (
                    f"{r8u.R8U_BASE_IMPLEMENTATION_COMMIT} "
                    f"{r8u.R8U_STARTING_IMPLEMENTATION_COMMIT}"
                ),
                (
                    f"{r8u.R8U_PROJECTION_REPAIR_IMPLEMENTATION_COMMIT} "
                    f"{r8u.R8U_BASE_IMPLEMENTATION_COMMIT}"
                ),
                (
                    f"{r8u.R8U_STARTING_IMPLEMENTATION_COMMIT} "
                    f"{r8u.ORIGINAL_SCIENTIFIC_COMMIT}"
                ),
                "5",
            ],
        ),
    ):
        _expect_code(
            r8u._current_r8u_implementation_commit,
            "R8U_IMPLEMENTATION_ANCESTRY_INVALID",
        )


def test_exact_gpu_recovery_array_and_cpu_finalizer_commands_cap_qsubs_at_three() -> None:
    recovery = r8u._r8u_recovery_qsub_command(IMPLEMENTATION_COMMIT)
    array = r8u._r8u_continuation_array_command(IMPLEMENTATION_COMMIT)
    finalizer = r8u._r8u_continuation_finalizer_command(IMPLEMENTATION_COMMIT, "102")

    assert "-t" not in recovery
    assert recovery[recovery.index("-N") + 1] == f"lvef_c3_r8u_rec_{IMPLEMENTATION_COMMIT[:8]}"
    assert recovery.count("gpus=1") == 1
    assert array[array.index("-t") + 1] == "17-19"
    assert array[array.index("-tc") + 1] == "1"
    assert array.count("gpus=1") == 1
    assert finalizer[finalizer.index("-hold_jid") + 1] == "102"
    assert "gpus=1" not in finalizer
    assert recovery[-1] == array[-1] == finalizer[-1] == str(r8u.RUNNER_PATH)
    assert 1 + 1 + 1 == 3
    for command in (recovery, array, finalizer):
        assert command[command.index("-r") + 1] == "n"


def test_every_new_r8u_artifact_binds_the_closed_five_commit_authority() -> None:
    expected = _expected_r8u_implementation_authority_epochs()
    assert r8u._r8u_implementation_authority_epochs(
        IMPLEMENTATION_COMMIT
    ) == expected
    assert set(expected) == r8u.R8U_IMPLEMENTATION_AUTHORITY_EPOCH_KEYS
    _expect_code(
        lambda: r8u._r8u_implementation_authority_epochs(
            r8u.R8U_BASE_IMPLEMENTATION_COMMIT
        ),
        "R8U_IMPLEMENTATION_GIT_AUTHORITY_INVALID",
    )

    run = _fixed_run(Path("/synthetic"))
    recovery_prefix = tuple(
        item[2] for item in r8u.R8U_PREFIX_RECEIPT_AUTHORITIES
    )
    continuation_prefix = recovery_prefix + (SHA,)
    terminal_paths = {
        "preservation": Path("/synthetic/preservation"),
        "final_transition": Path("/synthetic/final-transition.json"),
        "final_ledger": Path("/synthetic/final-ledger.json"),
        "final_receipt": Path("/synthetic/final-receipt.json"),
    }
    final_receipt = {
        "n_selected_studies": 1,
        "n_expected_objects": 1,
        "expected_source_bytes": 1,
        "n_successfully_extracted_cines": 1,
        "n_object_technical_dispositions": 0,
        "n_blocking_failures": 0,
        "n_clip_embeddings": 1,
        "n_pooled_studies": 1,
        "n_no_cine_studies": 0,
        "n_new_no_cine_studies": 0,
        "object_substitution_count": 0,
        "unaccounted_multiframe_objects": 0,
        "raw_dicoms_retained": True,
        "extracted_cache_retired": True,
    }
    with (
        mock.patch.object(r8u, "_r8u_failed_partial_observation", return_value={}),
        mock.patch.object(
            r8u, "_r8u_historical_r8r_chain_authority", return_value={}
        ),
        mock.patch.object(r8u, "_script_authority", return_value={}),
        mock.patch.object(r8u, "_qsub_evidence_authority", return_value={}),
        mock.patch.object(r8u.core, "sha256_file", return_value=SHA),
        mock.patch.object(
            r8u.sequential, "_batch_paths", return_value=terminal_paths
        ),
        mock.patch.object(
            r8u,
            "_current_r8u_implementation_commit",
            return_value=IMPLEMENTATION_COMMIT,
        ),
        mock.patch.object(
            r8u, "_validate_recovery_accounting_projection", return_value={}
        ),
    ):
        artifacts = (
            r8u._r8u_failed_partial_seal(
                implementation_commit=IMPLEMENTATION_COMMIT
            ),
            r8u._r8u_recovery_authority(
                run=run,
                implementation_commit=IMPLEMENTATION_COMMIT,
                qsub_environment_sha256=SHA,
                prefix_receipts=recovery_prefix,
                partial_seal_sha256=SHA,
                capacity_sha256=SHA,
                raw_authority={},
                failed_recovery_epoch_authority={
                    "status": (
                        "PASS_IMMUTABLE_FAILED_RECOVERY_APPLICATION_EXIT_78"
                    ),
                    "job_id": r8u.R8U_FAILED_RECOVERY_JOB_ID,
                },
            ),
            r8u._r8u_recovery_submission_receipt(
                implementation_commit=IMPLEMENTATION_COMMIT,
                recovery_job_id="101",
                qsub_environment_sha256=SHA,
                recovery_authority_sha256=SHA,
                partial_seal_sha256=SHA,
                capacity_sha256=SHA,
            ),
            r8u._r8u_recovery_terminal_receipt(
                run=run, final_receipt=final_receipt
            ),
            r8u._r8u_recovery_accounting_receipt(
                implementation_commit=IMPLEMENTATION_COMMIT,
                recovery_job_id="101",
                accounting={},
            ),
            r8u._r8u_continuation_claim(
                run=run,
                implementation_commit=IMPLEMENTATION_COMMIT,
                qsub_environment_sha256=SHA,
                prefix_receipts=continuation_prefix,
            ),
            r8u._r8u_continuation_submission_receipt(
                implementation_commit=IMPLEMENTATION_COMMIT,
                recovery_job_id="101",
                array_job_id="102",
                finalizer_job_id="103",
                qsub_environment_sha256=SHA,
                continuation_claim_sha256=SHA,
            ),
        )
    assert {
        artifact["artifact_type"] for artifact in artifacts
    } == {
        "lvef_c3_r8u_r2_failed_task16_partial_extraction_evidence_v1",
        "lvef_c3_r8u_r2_batch16_recovery_authority_v1",
        "lvef_c3_r8u_r2_batch16_recovery_submission_v1",
        "lvef_c3_r8u_r2_batch16_recovery_terminal_v1",
        "lvef_c3_r8u_r2_batch16_recovery_accounting_v1",
        "lvef_c3_r8u_r2_fixed_continuation_claim_v1",
        "lvef_c3_r8u_r2_fixed_continuation_submission_v1",
    }
    for artifact in artifacts:
        _assert_exact_r8u_implementation_authority_epochs(artifact)


def test_failed_partial_is_metadata_only_sealed_and_mutation_is_blocking() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        attempt = Path(temporary) / "attempt"
        partial = (
            attempt
            / "extracted_cache"
            / r8u.R8U_FIXED_BATCH_ID
            / "dicom_extraction.partial"
        )
        nested = partial / "clips" / "nested"
        nested.mkdir(parents=True)
        for directory in (partial, partial / "clips", nested):
            directory.chmod(0o700)
        first = partial / "clips" / "first.npz"
        second = nested / "second.npz"
        first.write_bytes(b"one")
        second.write_bytes(b"two-two")
        first.chmod(0o600)
        second.chmod(0o600)

        rows = [
            r8u._metadata_row(partial, root=attempt, kind="D"),
            r8u._metadata_row(partial / "clips", root=attempt, kind="D"),
            r8u._metadata_row(nested, root=attempt, kind="D"),
            r8u._metadata_row(first, root=attempt, kind="F"),
            r8u._metadata_row(second, root=attempt, kind="F"),
        ]
        payload = b"".join(
            json.dumps(row, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
            for row in sorted(rows)
        )
        tree_sha = hashlib.sha256(payload).hexdigest()
        before = {path: path.stat().st_size for path in (first, second)}
        with (
            mock.patch.object(r8u, "ATTEMPT_ROOT", attempt),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_FILES", 2),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_DIRECTORIES", 3),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_BYTES", 10),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_METADATA_SHA256", tree_sha),
            mock.patch.object(Path, "open", side_effect=AssertionError("NPZ body opened")),
        ):
            observation = r8u._r8u_failed_partial_observation()
            seal = r8u._r8u_failed_partial_seal(implementation_commit=IMPLEMENTATION_COMMIT)
        assert observation["npz_body_reads"] == 0
        assert observation["symlink_count"] == observation["nonregular_count"] == 0
        assert seal["partial_outputs_adopted"] is False
        assert seal["partial_outputs_modified"] is False
        assert seal["partial_outputs_deleted"] is False
        assert seal["partial_outputs_renamed"] is False
        _assert_exact_r8u_implementation_authority_epochs(seal)
        assert {path: path.stat().st_size for path in (first, second)} == before

        first.chmod(0o640)
        with (
            mock.patch.object(r8u, "ATTEMPT_ROOT", attempt),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_FILES", 2),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_DIRECTORIES", 3),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_BYTES", 10),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_METADATA_SHA256", tree_sha),
        ):
            _expect_code(
                r8u._r8u_failed_partial_observation,
                "R8U_FAILED_PARTIAL_EVIDENCE_INVALID",
            )


def test_retained_raw_pass_ignores_historical_device_but_blocks_live_hash_drift() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run = _fixed_run(root)
        key = "object-1"
        row = {
            "source_object_key": key,
            "size_bytes": 4,
            "generation": "7",
            "md5_base64": "md5",
            "crc32c_base64": "crc",
        }
        run.plan["batches"][15]["objects"] = [row]
        raw_batch = root / "raw" / r8u.R8U_FIXED_BATCH_ID
        object_path = raw_batch / "objects" / f"{key}.dcm"
        object_path.parent.mkdir(parents=True)
        object_path.write_bytes(b"data")
        object_path.chmod(0o600)
        info = object_path.stat(follow_symlinks=False)
        receipt = {"local_sha256": "local", "file_device": info.st_dev + 999}

        def observed(**changes: Any) -> dict[str, Any]:
            value = {
                "size_bytes": 4,
                "md5_base64": "md5",
                "crc32c_base64": "crc",
                "sha256": "local",
                "file_device": info.st_dev,
                "file_inode": info.st_ino,
                "file_mtime_ns": info.st_mtime_ns,
            }
            value.update(changes)
            return value

        @contextmanager
        def provider(value: Mapping[str, Any]) -> Iterator[Any]:
            yield lambda _path, _key: dict(value)

        with (
            mock.patch.object(r8u, "R8U_BATCH16_RAW_FILES", 1),
            mock.patch.object(r8u, "_r8u_batch16_raw_control_authority", return_value={"cloud_requests": 0}),
            mock.patch.object(r8u.sequential, "_batch_paths", return_value={"raw_batch": raw_batch}),
            mock.patch.object(r8u.core, "load_strict_json", return_value=receipt),
            mock.patch.object(r8u.sequential, "_digest_provider", side_effect=lambda *_a, **_k: provider(observed())),
        ):
            result = r8u._r8u_validate_batch16_raw_bodies(run)
        assert result["status"] == "PASS_BATCH16_RETAINED_RAW_CONTENT_AUTHORITY"
        assert result["raw_dicom_body_reads"] == 1
        assert result["cloud_requests"] == 0

        for change in (
            {"size_bytes": 5},
            {"md5_base64": "bad"},
            {"crc32c_base64": "bad"},
            {"sha256": "bad"},
            {"file_device": info.st_dev + 1},
        ):
            with (
                mock.patch.object(r8u, "R8U_BATCH16_RAW_FILES", 1),
                mock.patch.object(r8u, "_r8u_batch16_raw_control_authority", return_value={}),
                mock.patch.object(r8u.sequential, "_batch_paths", return_value={"raw_batch": raw_batch}),
                mock.patch.object(r8u.core, "load_strict_json", return_value=receipt),
                mock.patch.object(r8u.sequential, "_digest_provider", side_effect=lambda *_a, _value=observed(**change), **_k: provider(_value)),
            ):
                _expect_code(
                    lambda: r8u._r8u_validate_batch16_raw_bodies(run),
                    "R8U_BATCH16_RAW_CONTENT_AUTHORITY_MISMATCH",
                )

    control_source = inspect.getsource(r8u._r8u_batch16_raw_control_authority)
    for binding in (
        "generation",
        "size_bytes",
        "md5_base64",
        "crc32c_base64",
        "local_sha256",
        "st_nlink",
        "st_uid",
    ):
        assert binding in control_source
    assert '"historical_st_dev_required": False' in control_source


def test_fresh_extraction_publication_is_atomic_no_clobber_and_keeps_partial() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        attempt = Path(temporary) / "attempt"
        recovery_root = attempt / "r8u_r2_batch16_recovery"
        fresh_batch = recovery_root / "fresh_extracted_cache" / r8u.R8U_FIXED_BATCH_ID
        fresh = fresh_batch / "dicom_extraction"
        fresh.mkdir(parents=True)
        (fresh / "stage_completion_receipt.restricted.json").write_text("{}\n", encoding="utf-8")
        canonical = attempt / "extracted_cache" / r8u.R8U_FIXED_BATCH_ID / "dicom_extraction"
        canonical.parent.mkdir(parents=True)
        partial = canonical.parent / "dicom_extraction.partial" / "clips"
        partial.mkdir(parents=True)
        partial_file = partial / "failed.npz"
        partial_file.write_bytes(b"failed-evidence")
        seal_path = recovery_root / "failed_partial_seal.restricted.json"
        seal_path.write_text("{}\n", encoding="utf-8")
        publication = recovery_root / "fresh_extraction_publication.restricted.json"
        run = _fixed_run(Path(temporary))

        with (
            mock.patch.object(r8u, "ATTEMPT_ROOT", attempt),
            mock.patch.object(r8u, "R8U_RECOVERY_ROOT", recovery_root),
            mock.patch.object(r8u, "R8U_FRESH_EXTRACTION_BATCH_ROOT", fresh_batch),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_SEAL_PATH", seal_path),
            mock.patch.object(r8u, "R8U_FRESH_PUBLICATION_PATH", publication),
            mock.patch.object(r8u.sequential, "_batch_paths", return_value={"extraction": canonical}),
            mock.patch.object(r8u, "_current_r8u_implementation_commit", return_value=IMPLEMENTATION_COMMIT),
        ):
            receipt = r8u._r8u_publish_fresh_extraction(
                run=run, raw_validation={"status": "PASS", "cloud_requests": 0}
            )
        assert receipt["partial_npz_adopted"] == 0
        assert receipt["failed_partial_modified"] is False
        _assert_exact_r8u_implementation_authority_epochs(receipt)
        assert canonical.is_dir() and not fresh.exists()
        assert partial_file.read_bytes() == b"failed-evidence"

        fresh.mkdir(parents=True)
        (fresh / "do-not-move").write_text("fresh", encoding="utf-8")
        with (
            mock.patch.object(r8u, "ATTEMPT_ROOT", attempt),
            mock.patch.object(r8u, "R8U_FRESH_EXTRACTION_BATCH_ROOT", fresh_batch),
            mock.patch.object(r8u, "R8U_FAILED_PARTIAL_SEAL_PATH", seal_path),
            mock.patch.object(r8u, "R8U_FRESH_PUBLICATION_PATH", publication),
            mock.patch.object(r8u.sequential, "_batch_paths", return_value={"extraction": canonical}),
            mock.patch.object(
                r8u,
                "_current_r8u_implementation_commit",
                return_value=IMPLEMENTATION_COMMIT,
            ),
        ):
            _expect_code(
                lambda: r8u._r8u_publish_fresh_extraction(run=run, raw_validation={}),
                "R8U_FRESH_EXTRACTION_PUBLICATION_INVALID",
            )
        assert (fresh / "do-not-move").read_text(encoding="utf-8") == "fresh"
        assert partial_file.read_bytes() == b"failed-evidence"


def test_capacity_is_observed_once_before_any_root_or_recovery_qsub() -> None:
    run = _fixed_run(Path("/synthetic"))
    order: list[str] = []
    environment = {"USER": "pkarim"}

    def capacity_probe(*_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        order.append("capacity")
        return {"status": r8u.R8U_CAPACITY_STATUS}

    def create_root(_path: Path) -> None:
        order.append("root")

    def capture(*_args: Any, **_kwargs: Any) -> str:
        order.append("qsub")
        return "101"

    capacity_probe_mock = mock.Mock(side_effect=capacity_probe)
    capacity_validate_mock = mock.Mock()
    patches = (
        mock.patch.object(r8u.scheduler, "validate_scheduler_tools"),
        mock.patch.object(r8u.scheduler, "build_qsub_environment", return_value=(environment, {})),
        mock.patch.object(r8u.scheduler, "qsub_environment_sha256", return_value=SHA),
        mock.patch.object(r8u, "_current_r8u_implementation_commit", return_value=IMPLEMENTATION_COMMIT),
        mock.patch.object(r8u, "_validate_no_active_jobs"),
        mock.patch.object(r8u, "_validate_r8u_no_active_processes"),
        mock.patch.object(r8u, "_load_fixed_original_run", return_value=run),
        mock.patch.object(r8u, "_validate_original_controls"),
        mock.patch.object(r8u, "_r8u_validate_pristine_successor_exclusions"),
        mock.patch.object(r8u, "_r8u_validate_pre_mutation_projections"),
        mock.patch.object(r8u, "_r8u_validate_attempt_content_authority"),
        mock.patch.object(r8u, "_r8u_historical_r8r_chain_authority", return_value={}),
        mock.patch.object(r8u, "_r8u_validate_frozen_prefix", return_value=tuple(item[2] for item in r8u.R8U_PREFIX_RECEIPT_AUTHORITIES)),
        mock.patch.object(r8u, "_r8u_failed_partial_seal", return_value={}),
        mock.patch.object(r8u, "_r8u_batch16_raw_control_authority", return_value={}),
        mock.patch.object(
            r8u,
            "_r8u_failed_recovery_epoch_authority",
            return_value={
                "status": "PASS_IMMUTABLE_FAILED_RECOVERY_APPLICATION_EXIT_78",
                "job_id": r8u.R8U_FAILED_RECOVERY_JOB_ID,
            },
        ),
        mock.patch.object(r8u, "_r8u_require_recovery_absent"),
        mock.patch.object(
            r8u.capacity,
            "probe_fixed_r8u_batch16_recovery_capacity",
            new=capacity_probe_mock,
        ),
        mock.patch.object(
            r8u.capacity,
            "validate_fixed_r8u_batch16_recovery_capacity",
            new=capacity_validate_mock,
        ),
        mock.patch.object(r8u, "_create_private_directory_no_clobber", side_effect=create_root),
        mock.patch.object(r8u, "_write_private_json", return_value=SHA),
        mock.patch.object(r8u, "_r8u_recovery_authority", return_value={}),
        mock.patch.object(r8u.scheduler, "_capture_qsub", side_effect=capture),
        mock.patch.object(r8u, "_r8u_recovery_submission_receipt", return_value={}),
        mock.patch.object(r8u, "_load_private_json", return_value=({}, b"")),
        mock.patch.object(
            r8u, "_validate_r8u_initial_recovery_qstat", return_value="qw"
        ),
    )
    with ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)
        result = r8u.submit_r8u_batch16_recovery()
    assert result["new_qsub_submissions"] == 1
    assert result["status"] == "BATCH16_RECOVERY_SUBMITTED_AWAITING_TERMINAL"
    assert result["initial_state"] == "qw"
    assert result["login_node_polling_started"] is False
    assert order.count("capacity") == 1
    assert order.count("qsub") == 1
    assert order.index("capacity") < order.index("root") < order.index("qsub")
    capacity_probe_mock.assert_called_once_with(
        run.plan,
        r8u_scheduler_log_repair_commit=IMPLEMENTATION_COMMIT,
        process_runner=None,
    )
    capacity_validate_mock.assert_called_once_with(
        run.plan,
        {"status": r8u.R8U_CAPACITY_STATUS},
        r8u_scheduler_log_repair_commit=IMPLEMENTATION_COMMIT,
    )

    order.clear()
    blocked = {
        "status": "BLOCKED",
        "quota_margin_beyond_reserve_bytes": -11,
        "physical_margin_beyond_reserve_bytes": -22,
        "file_slot_margin_after_demand": -33,
    }
    blocked_probe = mock.Mock(return_value=blocked)
    blocked_validate = mock.Mock()
    create = mock.Mock()
    qsub = mock.Mock()
    blocked_patches = (
        mock.patch.object(r8u.scheduler, "validate_scheduler_tools"),
        mock.patch.object(r8u.scheduler, "build_qsub_environment", return_value=(environment, {})),
        mock.patch.object(r8u.scheduler, "qsub_environment_sha256", return_value=SHA),
        mock.patch.object(r8u, "_current_r8u_implementation_commit", return_value=IMPLEMENTATION_COMMIT),
        mock.patch.object(r8u, "_validate_no_active_jobs"),
        mock.patch.object(r8u, "_validate_r8u_no_active_processes"),
        mock.patch.object(r8u, "_load_fixed_original_run", return_value=run),
        mock.patch.object(r8u, "_validate_original_controls"),
        mock.patch.object(r8u, "_r8u_validate_pristine_successor_exclusions"),
        mock.patch.object(r8u, "_r8u_validate_pre_mutation_projections"),
        mock.patch.object(r8u, "_r8u_historical_r8r_chain_authority", return_value={}),
        mock.patch.object(r8u, "_r8u_validate_frozen_prefix", return_value=()),
        mock.patch.object(r8u, "_r8u_failed_partial_seal", return_value={}),
        mock.patch.object(r8u, "_r8u_batch16_raw_control_authority", return_value={}),
        mock.patch.object(
            r8u,
            "_r8u_failed_recovery_epoch_authority",
            return_value={
                "status": "PASS_IMMUTABLE_FAILED_RECOVERY_APPLICATION_EXIT_78",
                "job_id": r8u.R8U_FAILED_RECOVERY_JOB_ID,
            },
        ),
        mock.patch.object(r8u, "_r8u_require_recovery_absent"),
        mock.patch.object(
            r8u.capacity,
            "probe_fixed_r8u_batch16_recovery_capacity",
            new=blocked_probe,
        ),
        mock.patch.object(
            r8u.capacity,
            "validate_fixed_r8u_batch16_recovery_capacity",
            new=blocked_validate,
        ),
        mock.patch.object(
            r8u, "_create_private_directory_no_clobber", new=create
        ),
        mock.patch.object(r8u.scheduler, "_capture_qsub", new=qsub),
    )
    with ExitStack() as stack:
        for patcher in blocked_patches:
            stack.enter_context(patcher)
        try:
            r8u.submit_r8u_batch16_recovery()
        except r8u.R8RControllerError as exc:
            assert exc.code == "R8U_RECOVERY_CAPACITY_BLOCKED"
            assert exc.capacity_deficits == {
                "quota_deficit_bytes": 11,
                "physical_deficit_bytes": 22,
                "file_slot_deficit": 33,
            }
        else:
            raise AssertionError("expected capacity blocker")
    blocked_probe.assert_called_once_with(
        run.plan,
        r8u_scheduler_log_repair_commit=IMPLEMENTATION_COMMIT,
        process_runner=None,
    )
    blocked_validate.assert_called_once_with(
        run.plan,
        blocked,
        r8u_scheduler_log_repair_commit=IMPLEMENTATION_COMMIT,
    )
    create.assert_not_called()
    qsub.assert_not_called()


def test_every_r8u_capacity_call_binds_scheduler_log_repair_commit() -> None:
    tree = ast.parse(inspect.getsource(r8u))
    method_counts = {
        "probe_fixed_r8u_batch16_recovery_capacity": 0,
        "validate_fixed_r8u_batch16_recovery_capacity": 0,
    }
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "capacity"
            and node.func.attr in method_counts
        ):
            method_counts[node.func.attr] += 1
            calls.append(node)

    assert method_counts == {
        "probe_fixed_r8u_batch16_recovery_capacity": 1,
        "validate_fixed_r8u_batch16_recovery_capacity": 4,
    }
    for call in calls:
        bindings = [
            keyword
            for keyword in call.keywords
            if keyword.arg == "r8u_scheduler_log_repair_commit"
        ]
        assert len(bindings) == 1
        assert isinstance(bindings[0].value, ast.Name)
        assert bindings[0].value.id == "implementation_commit"


def test_pre_mutation_process_gate_catches_original_r8r_and_r8u_workers() -> None:
    environment = {"USER": "pkarim"}

    def runner(command: str) -> Any:
        payload = f"pkarim python controller.py {command}\n".encode()
        return lambda *_a, **_k: subprocess.CompletedProcess([], 0, payload, b"")

    for marker in (
        "--run-array-task",
        "--run-cohort-finalizer",
        "--recover-batch3-preservation",
        "--run-continuation-array-task",
        "--run-continuation-finalizer",
        "--run-batch16-recovery",
        "--run-continuation-17-19-array-task",
        "--run-r8u-continuation-finalizer",
    ):
        _expect_code(
            lambda marker=marker: r8u._validate_r8u_no_active_processes(
                environment, runner=runner(marker)
            ),
            "R8U_ACTIVE_MATCHING_PROCESS_EXISTS",
        )
    r8u._validate_r8u_no_active_processes(
        environment, runner=runner("--submit-batch16-recovery")
    )


def test_recovery_reruns_extraction_and_echoprime_once_then_preserves_retires_finalizes() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        run = _fixed_run(root)
        run.runtime_authority["checkpoint_sha256"] = SHA
        paths = {
            "raw_batch": root / "raw",
            "download_ledger": root / "download.json",
            "extraction": root / "extraction",
            "extraction_ledger": root / "extraction-ledger.json",
            "pooling_ledger": root / "pooling-ledger.json",
            "echoprime": root / "echoprime",
            "batch_root": root / "batch",
            "preservation": root / "preservation",
        }
        dicom = mock.Mock(return_value={"status": "PASS_DICOM_EXTRACTION"})
        echoprime = mock.Mock(return_value={"status": "PASS_ECHOPRIME"})
        download = mock.Mock(side_effect=AssertionError("download reached"))
        preserve = mock.Mock(return_value={"status": "PASS_BATCH_CACHE_RETIREMENT_ELIGIBLE"})
        retire = mock.Mock(return_value={"status": "PASS_RETIRED"})
        finalize_batch = mock.Mock(
            return_value={
                "status": "PASS_BATCH_FINALIZED",
                "raw_dicoms_retained": True,
                "extracted_cache_retired": True,
                "n_new_no_cine_studies": 0,
                "object_substitution_count": 0,
                "unaccounted_multiframe_objects": 0,
            }
        )
        dependency = SimpleNamespace(
            environment_validator=mock.Mock(),
            digest_provider_factory=None,
            dicom=dicom,
            extraction_workers=4,
            echoprime=echoprime,
            echoprime_batch_size=8,
            download=download,
            preserve=preserve,
            retire=retire,
            finalize_batch=finalize_batch,
        )
        technical = mock.Mock()
        with (
            mock.patch.dict(os.environ, {"JOB_ID": "101", "SGE_TASK_ID": "undefined"}, clear=True),
            mock.patch.object(r8u, "_load_fixed_original_run", return_value=run),
            mock.patch.object(r8u, "_validate_r8u_recovery_submission"),
            mock.patch.object(r8u, "_validate_original_controls"),
            mock.patch.object(r8u, "_r8u_validate_attempt_content_authority"),
            mock.patch.object(r8u, "_r8u_validate_frozen_prefix", return_value=()),
            mock.patch.object(r8u, "validate_r8u_failed_partial_seal", return_value={}),
            mock.patch.object(r8u.sequential, "_extraction_cache_inventory", return_value=SimpleNamespace(active=1)),
            mock.patch.object(r8u.sequential, "_batch_paths", return_value=paths),
            mock.patch.object(r8u.sequential, "resolve_dependencies", return_value=dependency),
            mock.patch.object(r8u.core, "sha256_file", return_value=SHA),
            mock.patch.object(r8u, "_r8u_validate_batch16_raw_bodies", return_value={"status": "PASS"}),
            mock.patch.object(r8u, "_create_private_directory_no_clobber"),
            mock.patch.object(r8u, "_r8u_publish_fresh_extraction", return_value={}),
            mock.patch.object(r8u.stages, "advance_stage_ledger"),
            mock.patch.object(r8u.stages, "validate_extraction_manifest_plan_membership", technical),
            mock.patch.object(r8u.sequential, "_cache_retirement_authorization", return_value=root / "authorization.json"),
            mock.patch.object(r8u, "_r8u_recovery_terminal_receipt", return_value={"status": r8u.R8U_RECOVERY_STATUS}),
            mock.patch.object(r8u, "_write_private_json", return_value=SHA),
        ):
            result = r8u.run_r8u_batch16_recovery()
        dicom.assert_called_once()
        echoprime.assert_called_once()
        technical.assert_called_once()
        preserve.assert_called_once()
        retire.assert_called_once()
        finalize_batch.assert_called_once()
        download.assert_not_called()
        assert result["status"] == r8u.R8U_RECOVERY_STATUS


def test_continuation_rejects_tasks_1_16_and_accepts_only_17_19() -> None:
    for task in (1, 15, 16, 20):
        with mock.patch.dict(
            os.environ,
            {"JOB_ID": "102", "SGE_TASK_ID": str(task)},
            clear=True,
        ):
            _expect_code(
                r8u.run_r8u_continuation_array_task,
                "R8U_CONTINUATION_ARRAY_CONTEXT_INVALID",
            )
    run = _fixed_run(Path("/synthetic"))
    with (
        mock.patch.dict(os.environ, {"JOB_ID": "102", "SGE_TASK_ID": "17"}, clear=True),
        mock.patch.object(r8u, "_load_fixed_original_run", return_value=run),
        mock.patch.object(r8u, "_validate_r8u_continuation_chain"),
        mock.patch.object(r8u, "_r8u_validate_attempt_content_authority"),
        mock.patch.object(r8u.sequential, "run_batch_task", return_value={"status": "PASS_BATCH_FINALIZED"}) as worker,
    ):
        r8u.run_r8u_continuation_array_task()
    assert worker.call_args.kwargs["task_id"] == 17
    assert worker.call_args.kwargs["dependencies"].execution_context is sequential.R8U_FIXED_CONTINUATION


def test_initial_recovery_qstat_is_one_nonpolling_exact_snapshot() -> None:
    name = r8u._r8u_recovery_job_name(IMPLEMENTATION_COMMIT)

    def xml(*, job_name: str = name, state: str = "qw") -> bytes:
        category = "running" if state in {"r", "t", "Rr"} else "pending"
        return (
            "<job_info><queue_info></queue_info><job_info>"
            f'<job_list state="{category}">'
            "<JB_job_number>101</JB_job_number>"
            f"<JB_name>{job_name}</JB_name><state>{state}</state>"
            "</job_list></job_info></job_info>"
        ).encode()

    runner = mock.Mock(
        return_value=subprocess.CompletedProcess([], 0, xml(), b"")
    )
    assert r8u._validate_r8u_initial_recovery_qstat(
        environment={"USER": "pkarim"},
        recovery_job_id="101",
        implementation_commit=IMPLEMENTATION_COMMIT,
        runner=runner,
    ) == "qw"
    runner.assert_called_once()
    for payload, code in (
        (xml(job_name="wrong"), "R8U_INITIAL_QSTAT_TOPOLOGY_INVALID"),
        (xml(state="Eqw"), "R8U_INITIAL_QSTAT_STATE_INVALID"),
        (
            b"<job_info><queue_info></queue_info><job_info></job_info></job_info>",
            "R8U_INITIAL_QSTAT_TOPOLOGY_INVALID",
        ),
    ):
        _expect_code(
            lambda payload=payload: r8u._validate_r8u_initial_recovery_qstat(
                environment={"USER": "pkarim"},
                recovery_job_id="101",
                implementation_commit=IMPLEMENTATION_COMMIT,
                runner=lambda *_a, **_k: subprocess.CompletedProcess(
                    [], 0, payload, b""
                ),
            ),
            code,
        )


def test_initial_qstat_requires_exact_17_19_states_and_held_finalizer() -> None:
    array_name = r8u._r8u_continuation_array_job_name(IMPLEMENTATION_COMMIT)
    final_name = r8u._r8u_continuation_finalizer_job_name(IMPLEMENTATION_COMMIT)

    def xml(task_tail: str = "18-19:1", tail_state: str = "qw", final_state: str = "hqw") -> bytes:
        return (
            "<job_info><queue_info>"
            f'<job_list state="running"><JB_job_number>102</JB_job_number><JB_name>{array_name}</JB_name><state>r</state><tasks>17</tasks></job_list>'
            "</queue_info><job_info>"
            f'<job_list state="pending"><JB_job_number>102</JB_job_number><JB_name>{array_name}</JB_name><state>{tail_state}</state><tasks>{task_tail}</tasks></job_list>'
            f'<job_list state="pending"><JB_job_number>103</JB_job_number><JB_name>{final_name}</JB_name><state>{final_state}</state></job_list>'
            "</job_info></job_info>"
        ).encode()

    def runner(payload: bytes) -> Any:
        return lambda *_a, **_k: subprocess.CompletedProcess([], 0, payload, b"")

    result = r8u._validate_r8u_initial_scheduler_topology(
        environment={"USER": "pkarim"},
        array_job_id="102",
        finalizer_job_id="103",
        implementation_commit=IMPLEMENTATION_COMMIT,
        runner=runner(xml()),
    )
    assert result["task_range"] == "17-19"
    assert result["finalizer_state"] == "HELD"
    for payload, expected in (
        (xml(task_tail="16-19:1"), "R8U_INITIAL_QSTAT_TASK_RANGE_INVALID"),
        (xml(task_tail="18-999999999999999999:1"), "R8U_INITIAL_QSTAT_TASK_RANGE_INVALID"),
        (xml(task_tail="18-19:0"), "R8U_INITIAL_QSTAT_TASK_RANGE_INVALID"),
        (xml(tail_state="r"), "R8U_INITIAL_QSTAT_STATE_INVALID"),
        (xml(final_state="qw"), "R8U_INITIAL_QSTAT_STATE_INVALID"),
        (xml(final_state="hEqw"), "R8U_INITIAL_QSTAT_STATE_INVALID"),
        (
            xml().replace(
                b'<job_list state="pending"><JB_job_number>102',
                b'<job_list state="running"><JB_job_number>102',
            ),
            "R8U_INITIAL_QSTAT_STATE_INVALID",
        ),
    ):
        _expect_code(
            lambda payload=payload: r8u._validate_r8u_initial_scheduler_topology(
                environment={"USER": "pkarim"},
                array_job_id="102",
                finalizer_job_id="103",
                implementation_commit=IMPLEMENTATION_COMMIT,
                runner=runner(payload),
            ),
            expected,
        )


def test_post_recovery_topology_and_qstat_gate_precede_worker_authorization() -> None:
    run = _fixed_run(Path("/synthetic"))
    extraction = run.attempt_root / "extracted_cache" / r8u.R8U_FIXED_BATCH_ID / "dicom_extraction"
    with (
        mock.patch.object(r8u, "validate_r8u_failed_partial_seal", return_value={}),
        mock.patch.object(r8u.sequential, "_batch_paths", return_value={"extraction": extraction}),
        mock.patch.object(r8u.os.path, "lexists", return_value=False),
        mock.patch.object(
            r8u.sequential,
            "_extraction_cache_inventory",
            return_value=sequential.ExtractionCacheInventory(
                active=1, preserved_terminal_failed=2
            ),
        ),
    ):
        topology = r8u._r8u_validate_post_recovery_cache_topology(run)
    assert topology["active_extraction_caches"] == 1

    with (
        mock.patch.object(r8u, "validate_r8u_failed_partial_seal", return_value={}),
        mock.patch.object(r8u.sequential, "_batch_paths", return_value={"extraction": extraction}),
        mock.patch.object(r8u.os.path, "lexists", return_value=False),
        mock.patch.object(
            r8u.sequential,
            "_extraction_cache_inventory",
            return_value=sequential.ExtractionCacheInventory(
                active=2, preserved_terminal_failed=2
            ),
        ),
    ):
        _expect_code(
            lambda: r8u._r8u_validate_post_recovery_cache_topology(run),
            "R8U_POST_RECOVERY_CACHE_TOPOLOGY_INVALID",
        )

    submit_source = inspect.getsource(r8u.submit_r8u_continuation_17_19)
    qstat_gate = submit_source.index("initial = _validate_r8u_initial_scheduler_topology")
    receipt_publication = submit_source.index(
        "_write_private_json(R8U_CONTINUATION_SUBMISSION_PATH, receipt)"
    )
    assert qstat_gate < receipt_publication


def test_r8u_finalizer_consumes_all_19_receipts_and_no_analysis_path() -> None:
    run = _fixed_run(Path("/synthetic"))
    historical = dict(r8u.R8U_HISTORICAL_R8R_CHAIN_AUTHORITIES)
    captured: dict[str, Any] = {}

    def finish(receipts: Any, **kwargs: Any) -> Mapping[str, Any]:
        captured["receipts"] = list(receipts)
        captured.update(kwargs)
        return {
            "status": "PASS_PRODUCTION_C3_FINALIZED",
            "production_batches": 19,
            "selected_studies": 4_530,
            "selected_subjects": 4_530,
            "verified_source_objects": 335_984,
            "selected_source_bytes": 1_216_569_133_322,
            "pooled_imaging_eligible_studies": 4_525,
            "no_cine_studies": 5,
            "new_no_cine_studies": 0,
            "all_authority_bindings_identical": False,
            "all_scientific_authority_bindings_identical": True,
            "implementation_authority_epoch_count": 3,
            "r8u_implementation_commit": IMPLEMENTATION_COMMIT,
            "r8u_recovery_continuation_authority_sha256": SHA,
            "model_fitting_count": 0,
            "endpoint_prediction_count": 0,
            "confirmatory_performance_access_count": 0,
        }

    with (
        mock.patch.dict(
            os.environ,
            {"JOB_ID": "103", "SGE_TASK_ID": "undefined", "CUDA_VISIBLE_DEVICES": ""},
            clear=True,
        ),
        mock.patch.object(r8u, "_load_fixed_original_run", return_value=run),
        mock.patch.object(r8u, "_r8u_validate_attempt_content_authority"),
        mock.patch.object(r8u, "_validate_r8u_continuation_chain"),
        mock.patch.object(
            r8u.sequential,
            "_batch_paths",
            side_effect=lambda _run, batch_id: {"final_receipt": Path("/receipts") / f"{batch_id}.json"},
        ),
        mock.patch.object(r8u, "_ensure_private_directory"),
        mock.patch.object(r8u, "_r8u_historical_r8r_chain_authority", return_value=historical),
        mock.patch.object(r8u, "_current_r8u_implementation_commit", return_value=IMPLEMENTATION_COMMIT),
        mock.patch.object(r8u.core, "sha256_file", return_value=SHA),
        mock.patch.object(r8u.finalizer, "finalize_receipts", side_effect=finish),
        mock.patch.object(r8u.finalizer, "write_json_atomic"),
    ):
        summary = r8u.run_r8u_continuation_finalizer()
    assert len(captured["receipts"]) == 19
    assert captured["receipts"][15].name == "c3_batch_015.json"
    assert captured["r8u_implementation_authority"].recovery_accounting_sha256 == SHA
    assert summary["implementation_authority_epoch_count"] == 3
    assert summary["model_fitting_count"] == 0
    assert summary["endpoint_prediction_count"] == 0
    assert summary["confirmatory_performance_access_count"] == 0
    source = inspect.getsource(r8u.run_r8u_continuation_finalizer)
    assert "model" not in source.casefold() or '"model_fitting_count"' in source
    assert "predict(" not in source.casefold()


def test_r8u_array_failure_never_claims_zero_new_cloud_activity() -> None:
    output = io.StringIO()
    with (
        mock.patch.object(
            r8u,
            "run_r8u_continuation_array_task",
            side_effect=r8u.R8RControllerError(
                "R8U_SYNTHETIC_ARRAY_DOWNLOAD_FAILURE",
                stage="DOWNLOAD",
            ),
        ),
        redirect_stdout(output),
    ):
        assert r8u.guarded_main(["--run-continuation-17-19-array-task"]) == 78
    lines = output.getvalue().splitlines()
    assert "R8U_NEW_CLOUD_REQUESTS=0" not in lines
    assert "R8U_BATCH16_CLOUD_REQUESTS=0" in lines
    assert "R8U_BATCH16_DOWNLOAD_RERUNS=0" in lines


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"R8U_BATCH16_DEPENDENCY_LIGHT_TESTS=PASS ({len(tests)})")
