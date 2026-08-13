from __future__ import annotations

"""One exact-live-path, synthetic-only Phase 1H-R2 acceptance gate.

The sole collected test in this module keeps every negative proof in helper
functions.  It never reads an SCC path and substitutes only the irreversible
cloud-transfer, qsub-submission, and GPU-execution boundaries.
"""

import base64
import copy
import csv
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _assert_live_scheduler_tool_constants(materializer) -> None:
    scheduler_root = Path(
        "/usr/local/ogs-ge2011.11.p1/sge_root/bin/linux-x64"
    )
    assert materializer.SCC_QSUB_LEXICAL_PATH == scheduler_root / "qsub"
    assert materializer.SCC_QSTAT_LEXICAL_PATH == scheduler_root / "qstat"

import lvef_c3_canary as canary
import lvef_c3_canary_dispatch as dispatch
import lvef_c3_canary_execution_authority as execution_authority
import lvef_c3_canary_manifest as manifest_contract
import lvef_c3_canary_scheduler_plan as scheduler_contract
import lvef_c3_canary_stage_worker as stage_worker
import lvef_c3_orchestration_core as core
import lvef_c3_production_stages as production_stages
import preserve_lvef_c3_production_batch as preservation


RUN_ID = "lvef_c3_exact_five_canary_ab12cd34"
BRANCH = "codex/lvef-multitask-revalidation"


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _write(path: Path, payload: bytes, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)
    return path


def _write_csv(
    path: Path, columns: tuple[str, ...], rows: list[Mapping[str, Any]]
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(
            {
                key: str(value).lower() if isinstance(value, bool) else value
                for key, value in row.items()
            }
            for row in rows
        )
    path.chmod(0o600)
    return path


def _candidate_rows(count: int = 5, *, split: str = "train") -> list[dict[str, Any]]:
    return [
        {
            "study_id": str(910_000 + index),
            "subject_id": str(810_000 + index),
            "split": split,
            "expected_object_count": 1,
            "expected_byte_total": index,
            "known_no_cine": False,
            "prior_reconstruction_smoke": False,
        }
        for index in range(1, count + 1)
    ]


def _source_rows(candidates: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        subject_id = str(candidate["subject_id"])
        study_id = str(candidate["study_id"])
        relative = (
            f"files/p{int(subject_id) // 1_000_000:02d}/p{subject_id}/"
            f"s{study_id}/synthetic_{index:03d}.dcm"
        )
        rows.append(
            {
                "subject_id": subject_id,
                "study_id": study_id,
                "split": str(candidate["split"]),
                "source_object_key": hashlib.sha256(
                    f"{manifest_contract.SOURCE_RELEASE}\0{relative}".encode("utf-8")
                ).hexdigest(),
                "source_relative_path": relative,
                "size_bytes": int(candidate["expected_byte_total"]),
                "generation": str(index),
                "md5_base64": base64.b64encode(bytes([index]) * 16).decode("ascii"),
                "crc32c_base64": base64.b64encode(bytes([index]) * 4).decode("ascii"),
            }
        )
    return rows


def _expect_code(
    code: str,
    operation: Callable[[], object],
    error_types: type[BaseException] | tuple[type[BaseException], ...],
) -> BaseException:
    try:
        operation()
    except error_types as exc:
        assert getattr(exc, "code", str(exc)) == code, (getattr(exc, "code", str(exc)), code)
        return exc
    raise AssertionError(f"expected {code}")


def _main_result(arguments: list[str]) -> tuple[int, str]:
    output = io.StringIO()
    with redirect_stdout(output):
        status = canary.main(arguments)
    return status, output.getvalue()


def _assert_main_pass(arguments: list[str], **kwargs: Any) -> str:
    output = io.StringIO()
    with redirect_stdout(output):
        status = canary.main(arguments, **kwargs)
    text = output.getvalue()
    assert status == 0, (arguments, status, text)
    assert "LVEF_C3_CANARY_CONTROL=PASS" in text
    return text


def _assert_main_blocked(
    arguments: list[str], code: str, **kwargs: Any
) -> str:
    output = io.StringIO()
    with redirect_stdout(output):
        status = canary.main(arguments, **kwargs)
    text = output.getvalue()
    assert status == 78, (arguments, status, text)
    assert f"LVEF_C3_CANARY_CONTROL=BLOCKED_{code}" in text, (
        code,
        text,
    )
    return text


def _assert_no_effects(effects: Mapping[str, mock.Mock]) -> None:
    for name, effect in effects.items():
        assert effect.call_count == 0, (name, effect.call_count)


def _assert_production_function_identities() -> None:
    assert canary.PRODUCTION_FUNCTIONS == {
        "source_transfer": core.execute_exact_batch_download,
        "download_integrity": core.verify_downloaded_partial,
        "dicom_stage": production_stages.run_production_dicom_extraction,
        "dicom_rows": production_stages.validate_production_dicom_rows,
        "extraction_rows": production_stages.validate_production_extraction_rows,
        "echoprime_stage": production_stages.run_production_echoprime,
        "embedding_values": production_stages.validate_embedding_values,
        "temporal_sampling": canary.reconstruction.temporal_sample,
        "encoder_input": canary.reconstruction._prepare_encoder_input,
        "study_mean_pooling": preservation.mean_pool_study_embeddings,
        "pooling_records": preservation.validate_study_pooling_records,
        "preservation": preservation.preserve_batch,
        "canary_finalization": canary.finalizer.finalize_canary_preservation_receipt,
    }


def _guard_irreversible_effects(stack: ExitStack) -> dict[str, mock.Mock]:
    effects: dict[str, mock.Mock] = {}
    for name, module, attribute in (
        ("cloud_transfer", core, "execute_exact_batch_download"),
        ("gpu", production_stages, "run_production_echoprime"),
    ):
        effect = stack.enter_context(
            mock.patch.object(
                module,
                attribute,
                side_effect=AssertionError(f"forbidden live effect reached: {name}"),
            )
        )
        effects[name] = effect
    return effects


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        },
    )
    return completed.stdout.strip()


def _build_clean_git_sandbox(root: Path) -> tuple[Path, str, str]:
    """Create a real clean branch/origin authority without any network use."""

    repository = root / "tracked-worktree"
    shutil.copytree(
        ROOT,
        repository,
        ignore=shutil.ignore_patterns(".git", ".DS_Store", "__pycache__", ".pytest_cache"),
    )
    _git(repository, "init", "-b", BRANCH)
    _git(repository, "config", "user.name", "Synthetic acceptance fixture")
    _git(repository, "config", "user.email", "synthetic.invalid@example.invalid")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "synthetic authority ancestor")
    ancestor = _git(repository, "rev-parse", "HEAD")

    state_path = repository / "configs/lvef_c3_execution_state_v1.yaml"
    state_value = json.loads(state_path.read_text(encoding="utf-8"))
    state_value["starting_authority_commit"] = ancestor
    state_path.write_text(
        json.dumps(state_value, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    _git(repository, "add", "configs/lvef_c3_execution_state_v1.yaml")
    _git(repository, "commit", "-m", "bind synthetic starting authority")
    governing_commit = _git(repository, "rev-parse", "HEAD")
    _git(
        repository,
        "update-ref",
        f"refs/remotes/origin/{BRANCH}",
        governing_commit,
    )
    assert _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == ""
    return repository, ancestor, governing_commit


def _build_synthetic_authority_inputs(
    root: Path, *, repository: Path, governing_commit: str
) -> dict[str, Any]:
    private_root = root / "owner-private" / "exact_five_canary"
    private_root.mkdir(parents=True, mode=0o700)
    private_root.chmod(0o700)
    inputs = private_root / "synthetic-inputs"
    inputs.mkdir(mode=0o700)
    candidate_csv = _write_csv(
        inputs / "candidate-studies.restricted.csv",
        manifest_contract.CANDIDATE_COLUMNS,
        _candidate_rows(),
    )
    source_object_csv = _write_csv(
        inputs / "source-objects.restricted.csv",
        manifest_contract.SOURCE_OBJECT_COLUMNS,
        _source_rows(_candidate_rows()),
    )
    environment_receipt = _write(
        inputs / "environment.restricted.json",
        (
            json.dumps(
                canary.synthetic_environment_receipt(governing_commit),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8"),
    )
    gcloud_receipt = _write(
        inputs / "gcloud-resolution.restricted.json",
        b'{"status":"PASS_SYNTHETIC_NO_CREDENTIAL_ACCESS"}\n',
    )
    cloudsdk_config = inputs / "cloudsdk"
    cloudsdk_config.mkdir(mode=0o700)
    _write(
        cloudsdk_config / "application_default_credentials.json",
        b"synthetic placeholder; acceptance must never read these bytes\n",
    )

    public = root / "synthetic-runtime"
    public.mkdir(mode=0o700)
    checkpoint = _write(
        public / "echoprime-encoder.pt", canary.SYNTHETIC_CHECKPOINT_BYTES
    )
    gcloud = _write(public / "gcloud", b"#!/bin/sh\nexit 97\n", 0o700)
    qsub = _write(public / "qsub", b"#!/bin/sh\nexit 97\n", 0o700)
    crc_python = _write(public / "crc-python", b"#!/bin/sh\nexit 97\n", 0o700)
    crc_worker = _write(public / "crc-worker.py", b"# synthetic crc worker\n")
    execution_state_path = repository / "configs/lvef_c3_execution_state_v1.yaml"
    return {
        "tracked_worktree": repository,
        "execution_state_path": execution_state_path,
        "private_root": private_root,
        "lifecycle_root": private_root / "lifecycle_state",
        "lifecycle_path": private_root
        / "lifecycle_state"
        / "canary_state.restricted.json",
        "authority_path": private_root / "execution_authorization_v1.json",
        "candidate_csv": candidate_csv,
        "source_object_csv": source_object_csv,
        "governing_commit": governing_commit,
        "run_id": RUN_ID,
        "production_contract_path": repository / "configs/lvef_c3_orchestration_v2.yaml",
        "state_machine_path": repository / "configs/lvef_c3_state_machine_v2.json",
        "resume_ledger_path": repository / "configs/lvef_c3_resume_ledger_v2.json",
        "scheduler_plan_path": repository
        / "configs/lvef_c3_canary_scheduler_plan_v1.json",
        "environment_receipt_path": environment_receipt,
        "checkpoint_path": checkpoint,
        "gcloud_binary_path": gcloud,
        "gcloud_resolution_receipt_path": gcloud_receipt,
        "cloudsdk_config_path": cloudsdk_config,
        "crc32c_python_path": crc_python,
        "crc32c_worker_path": crc_worker,
        "crc32c_distribution_sha256": "d" * 64,
        "qsub_path": qsub,
        "stage_worker_path": repository / "scripts/lvef_c3_canary_stage_worker.py",
        "stage_launcher_path": repository / "scripts/scc_run_lvef_c3_canary_stage.sh",
        "requester_pays_billing_project": "synthetic-private-project",
        "source_metadata_sha256": _sha_file(source_object_csv),
        "split_map_sha256": _sha_file(candidate_csv),
        "launch_authority_sha256": "e" * 64,
        "synthetic_mode": True,
    }


def _execution_authority_paths(
    private_root: Path, repository: Path
) -> execution_authority.CanaryExecutionAuthorityPaths:
    return execution_authority.CanaryExecutionAuthorityPaths(
        private_root=private_root,
        fixed_path=private_root / "execution_authorization_v1.json",
        canary_run_root=private_root / "canary_runs",
        tracked_worktree=repository,
        production_contract_path=repository / "configs/lvef_c3_orchestration_v2.yaml",
        execution_state_path=repository / "configs/lvef_c3_execution_state_v1.yaml",
        state_machine_path=repository / "configs/lvef_c3_state_machine_v2.json",
        resume_ledger_path=repository / "configs/lvef_c3_resume_ledger_v2.json",
        stage_worker_path=repository / "scripts/lvef_c3_canary_stage_worker.py",
        stage_launcher_path=repository / "scripts/scc_run_lvef_c3_canary_stage.sh",
    )


def _write_authority_packet(path: Path, packet: dict[str, Any]) -> None:
    packet.pop("authorization_sha256", None)
    packet["authorization_sha256"] = (
        execution_authority.calculate_authorization_sha256(packet)
    )
    path.write_bytes(execution_authority.serialize_authorization(packet))
    path.chmod(0o600)


def _assert_packet_tamper_failures(
    *,
    authority_path: Path,
    authority_paths: execution_authority.CanaryExecutionAuthorityPaths,
    governing_commit: str,
) -> dict[str, Any]:
    def load() -> dict[str, Any]:
        return execution_authority.load_and_validate_execution_authority(
            authority_path,
            expected_governing_commit=governing_commit,
            paths=authority_paths,
        )

    normalized = load()
    original_packet = authority_path.read_bytes()

    authority_path.write_bytes(original_packet + b"\n")
    authority_path.chmod(0o600)
    _expect_code(
        "CANARY_EXECUTION_AUTHORITY_SERIALIZATION_NOT_CANONICAL",
        load,
        execution_authority.CanaryExecutionAuthorityError,
    )
    authority_path.write_bytes(original_packet)
    authority_path.chmod(0o600)

    packet = json.loads(original_packet)
    packet["scheduler_plan"]["canonical_sha256"] = "0" * 64
    _write_authority_packet(authority_path, packet)
    _expect_code(
        "CANARY_EXECUTION_MANIFEST_SCHEDULER_BINDING_MISMATCH",
        load,
        execution_authority.CanaryExecutionAuthorityError,
    )
    authority_path.write_bytes(original_packet)
    authority_path.chmod(0o600)

    packet = json.loads(original_packet)
    preselection_path = Path(packet["preselection_authority"]["path"])
    original_preselection = preselection_path.read_bytes()
    preselection = json.loads(original_preselection)
    preselection["status"] = "NOT_AUTHORIZED"
    preselection_path.write_bytes(
        execution_authority.serialize_preselection_authority(preselection)
    )
    preselection_path.chmod(0o600)
    packet["preselection_authority"]["file_sha256"] = _sha_file(
        preselection_path
    )
    _write_authority_packet(authority_path, packet)
    _expect_code(
        "CANARY_PRESELECTION_AUTHORITY_INVALID",
        load,
        execution_authority.CanaryExecutionAuthorityError,
    )
    preselection_path.write_bytes(original_preselection)
    preselection_path.chmod(0o600)
    authority_path.write_bytes(original_packet)
    authority_path.chmod(0o600)

    packet = json.loads(original_packet)
    grant_path = Path(
        packet["stage_authorizations"]["DICOM_EXTRACTION"]["path"]
    )
    original_grant = grant_path.read_bytes()
    grant = json.loads(original_grant)
    grant["attempt_id"] = "lvef_c3_exact_five_canary_deadbeef"
    grant_path.write_bytes(
        execution_authority.canonical_json_bytes(grant) + b"\n"
    )
    grant_path.chmod(0o600)
    packet["stage_authorizations"]["DICOM_EXTRACTION"]["file_sha256"] = (
        _sha_file(grant_path)
    )
    _write_authority_packet(authority_path, packet)
    _expect_code(
        "CANARY_EXECUTION_SCIENTIFIC_AUTHORIZATION_INVALID",
        load,
        execution_authority.CanaryExecutionAuthorityError,
    )
    grant_path.write_bytes(original_grant)
    grant_path.chmod(0o600)
    authority_path.write_bytes(original_packet)
    authority_path.chmod(0o600)
    return normalized


def _patch_control_paths(
    stack: ExitStack, *, repository: Path, starting_ancestor: str
) -> None:
    def sandbox_path(path: Path) -> Path:
        return repository / Path(path).relative_to(ROOT)

    stack.enter_context(
        mock.patch.multiple(
            canary,
            REPOSITORY_ROOT=repository,
            DEFAULT_EXECUTION_STATE=repository
            / "configs/lvef_c3_execution_state_v1.yaml",
            DEFAULT_ORCHESTRATION_CONTRACT=repository
            / "configs/lvef_c3_orchestration_v2.yaml",
            DEFAULT_SCHEDULER_PLAN=repository
            / "configs/lvef_c3_canary_scheduler_plan_v1.json",
            PHASE1HR1_STARTING_AUTHORITY_COMMIT=starting_ancestor,
            PHASE1HR2_STARTING_AUTHORITY_COMMIT=starting_ancestor,
            TRACKED_CANARY_CONTROL_FILES=tuple(
                sandbox_path(Path(path))
                for path in canary.TRACKED_CANARY_CONTROL_FILES
            ),
        )
    )


def _assert_manifest_scope_failures() -> None:
    candidates = _candidate_rows()
    selected = manifest_contract.select_exact_five(candidates)
    sealed = manifest_contract.build_sealed_manifest(
        selected_studies=selected,
        source_objects=_source_rows(candidates),
        source_authority_commit="a" * 40,
        source_manifest_sha256="b" * 64,
        source_configuration_hashes={"synthetic": "c" * 64},
    )

    four = copy.deepcopy(sealed["manifest"])
    four["studies"].pop()
    four["study_count"] = 4
    four["subject_count"] = 4
    four["expected_object_count"] = 4
    four["expected_byte_total"] = sum(
        row["expected_byte_total"] for row in four["studies"]
    )
    _expect_code(
        "CANARY_EXACT_FIVE_COUNT_INVALID",
        lambda: manifest_contract.seal_manifest(four),
        manifest_contract.CanaryManifestError,
    )

    six = copy.deepcopy(sealed["manifest"])
    extra = copy.deepcopy(six["studies"][-1])
    extra["study_id"] = "999999"
    extra["subject_id"] = "899999"
    extra["selection_stratum"] = "unexpected_sixth_stratum"
    six["studies"].append(extra)
    six["study_count"] = 6
    six["subject_count"] = 6
    six["expected_object_count"] = 6
    six["expected_byte_total"] += extra["expected_byte_total"]
    _expect_code(
        "CANARY_EXACT_FIVE_COUNT_INVALID",
        lambda: manifest_contract.seal_manifest(six),
        manifest_contract.CanaryManifestError,
    )

    for split in ("val", "test"):
        _expect_code(
            "CANDIDATE_ELIGIBLE_UNIQUE_SUBJECTS_INSUFFICIENT",
            lambda split=split: manifest_contract.select_exact_five(
                _candidate_rows(split=split)
            ),
            manifest_contract.CanaryManifestError,
        )

    object_overflow = _candidate_rows()
    object_overflow[0]["expected_object_count"] = 751
    _expect_code(
        "CANARY_OBJECT_CEILING_EXCEEDED",
        lambda: manifest_contract.select_exact_five(object_overflow),
        manifest_contract.CanaryManifestError,
    )
    byte_overflow = _candidate_rows()
    byte_overflow[0]["expected_byte_total"] = 5_000_000_001
    _expect_code(
        "CANARY_BYTE_CEILING_EXCEEDED",
        lambda: manifest_contract.select_exact_five(byte_overflow),
        manifest_contract.CanaryManifestError,
    )


def _assert_state_failures(
    canary_state: Any,
    *,
    root: Path,
    execution_state_path: Path,
    governing_commit: str,
) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    root.parent.chmod(0o700)
    initialized = canary_state.initialize_state(
        root=root,
        execution_state_path=execution_state_path,
        governing_commit=governing_commit,
        run_id=RUN_ID,
    )
    assert initialized["current_state"] == "PRECANARY_READY"
    state_path = root / "canary_state.restricted.json"
    original = state_path.read_bytes()

    _expect_code(
        "CANARY_STATE_EXPECTED_CURRENT_MISMATCH",
        lambda: canary_state.transition_state(
            root=root,
            execution_state_path=execution_state_path,
            expected_current="CANARY_AUTHORITY_PREPARED",
            target_state="CANARY_MANIFEST_SEALED",
            governing_commit=governing_commit,
            run_id=RUN_ID,
            reason_code="SYNTHETIC_WRONG_CURRENT",
        ),
        canary_state.CanaryStateError,
    )
    assert state_path.read_bytes() == original

    _expect_code(
        "CANARY_STATE_GOVERNING_COMMIT_MISMATCH",
        lambda: canary_state.transition_state(
            root=root,
            execution_state_path=execution_state_path,
            expected_current="PRECANARY_READY",
            target_state="CANARY_AUTHORITY_PREPARED",
            governing_commit="f" * 40,
            run_id=RUN_ID,
            reason_code="SYNTHETIC_WRONG_COMMIT",
        ),
        canary_state.CanaryStateError,
    )
    assert state_path.read_bytes() == original

    _expect_code(
        "CANARY_STATE_TRANSITION_NOT_PERMITTED",
        lambda: canary_state.transition_state(
            root=root,
            execution_state_path=execution_state_path,
            expected_current="PRECANARY_READY",
            target_state="CANARY_MANIFEST_SEALED",
            governing_commit=governing_commit,
            run_id=RUN_ID,
            reason_code="SYNTHETIC_FORWARD_EDGE_SKIPPED",
        ),
        canary_state.CanaryStateError,
    )
    assert state_path.read_bytes() == original


def _assert_materialization_result(result: Any) -> None:
    assert result.preselection_identifier_fields == 0
    assert result.state["current_state"] == "CANARY_MANIFEST_SEALED"
    assert result.state["transition_count"] == 2
    assert [row["target_state"] for row in result.state["history"]] == [
        "CANARY_AUTHORITY_PREPARED",
        "CANARY_MANIFEST_SEALED",
    ]
    assert result.manifest_study_count == 5
    assert result.manifest_subject_count == 5
    assert 1 <= result.manifest_object_count <= 750
    assert 1 <= result.manifest_byte_count <= 5_000_000_000


def _assert_missing_producer_fails(
    root: Path, *, relative_path: str
) -> None:
    repository, ancestor, _ = _build_clean_git_sandbox(root)
    missing = repository / relative_path
    _git(repository, "rm", relative_path)
    _git(repository, "commit", "-m", f"synthetic missing {missing.name}")
    head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "update-ref", f"refs/remotes/origin/{BRANCH}", head)
    runtime = canary.CanaryControlRuntime(
        repository_root=repository,
        execution_state_path=repository / "configs/lvef_c3_execution_state_v1.yaml",
        orchestration_contract_path=repository
        / "configs/lvef_c3_orchestration_v2.yaml",
        scheduler_plan_path=repository
        / "configs/lvef_c3_canary_scheduler_plan_v1.json",
        synthetic_external_effects=True,
    )
    with ExitStack() as stack:
        _patch_control_paths(
            stack, repository=repository, starting_ancestor=ancestor
        )
        _assert_main_blocked(
            ["--validate-installation"],
            "CANARY_TRACKED_CONTROL_FILE_MISSING",
            runtime=runtime,
        )


# The fixture and principal test are completed against the tracked R2
# materializer/state APIs below.  Keeping all helpers non-``test_`` preserves
# exactly one collected release gate in this module.


def test_exact_live_canary_cli_path_passes_in_synthetic_sandbox() -> None:
    # Imports are local so absence of either required producer is itself an
    # explicit acceptance failure, not a silently substituted test helper.
    import lvef_c3_canary_authority_materializer as materializer
    import lvef_c3_canary_state as canary_state

    assert callable(materializer.prepare_live_authority)
    assert callable(canary_state.initialize_state)
    assert callable(canary_state.transition_state)
    _assert_live_scheduler_tool_constants(materializer)

    _assert_manifest_scope_failures()
    _assert_production_function_identities()

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        repository, starting_ancestor, governing_commit = _build_clean_git_sandbox(
            root
        )
        _assert_missing_producer_fails(
            root / "missing-state-producer",
            relative_path="scripts/lvef_c3_canary_state.py",
        )
        _assert_missing_producer_fails(
            root / "missing-materializer",
            relative_path="scripts/lvef_c3_canary_authority_materializer.py",
        )
        sandbox_root = root / "synthetic-private-root"
        sandbox_root.mkdir(mode=0o700)
        source = materializer.build_synthetic_materialization_authority_source(
            sandbox_root,
            repository=repository,
            governing_commit=governing_commit,
            run_id=RUN_ID,
        )
        execution_state_path = repository / "configs/lvef_c3_execution_state_v1.yaml"
        private_root = (
            source.production_root / "owner_private" / "exact_five_canary"
        )
        lifecycle_root = private_root / "lifecycle_state"
        authority_path = private_root / "execution_authorization_v1.json"
        authority_paths = _execution_authority_paths(private_root, repository)
        submitted: list[tuple[str, ...]] = []

        def qsub_submitter(command: Any) -> str:
            submitted.append(tuple(str(value) for value in command))
            return str(92_000 + len(submitted))

        runtime = canary.CanaryControlRuntime(
            repository_root=repository,
            execution_state_path=execution_state_path,
            orchestration_contract_path=repository
            / "configs/lvef_c3_orchestration_v2.yaml",
            scheduler_plan_path=repository
            / "configs/lvef_c3_canary_scheduler_plan_v1.json",
            lifecycle_root=lifecycle_root,
            authority_path=authority_path,
            authority_paths=authority_paths,
            materialization_authority_source=source,
            qsub_submitter=qsub_submitter,
            synthetic_external_effects=True,
        )
        with ExitStack() as stack:
            _patch_control_paths(
                stack,
                repository=repository,
                starting_ancestor=starting_ancestor,
            )
            effects = _guard_irreversible_effects(stack)

            validation = _assert_main_pass(
                ["--validate-installation"], runtime=runtime
            )
            assert "QSUB_SUBMISSIONS=0" in validation
            _assert_no_effects(effects)

            dirty = repository / "synthetic-untracked-dirty-marker"
            dirty.write_text("must block\n", encoding="utf-8")
            _assert_main_blocked(
                ["--validate-installation"],
                "CANARY_TRACKED_WORKTREE_DIRTY",
                runtime=runtime,
            )
            dirty.unlink()
            tracked_state_bytes = execution_state_path.read_bytes()
            execution_state_path.write_bytes(tracked_state_bytes + b"\n")
            _assert_main_blocked(
                ["--validate-installation"],
                "CANARY_TRACKED_WORKTREE_DIRTY",
                runtime=runtime,
            )
            execution_state_path.write_bytes(tracked_state_bytes)

            _assert_state_failures(
                canary_state,
                root=root / "state-negative" / "lifecycle_state",
                execution_state_path=execution_state_path,
                governing_commit=governing_commit,
            )

            # A real state-root failure must clean the discovery transaction
            # and leave the previously absent lifecycle snapshot absent.
            rollback_root = root / "rollback-private-root"
            rollback_root.mkdir(mode=0o700)
            rollback_source = (
                materializer.build_synthetic_materialization_authority_source(
                    rollback_root,
                    repository=repository,
                    governing_commit=governing_commit,
                    run_id="lvef_c3_exact_five_canary_fe12dc34",
                )
            )
            rollback_config = materializer.discover_live_materialization_config(
                repository=repository,
                execution_state_path=execution_state_path,
                authority_source=rollback_source,
            )
            rollback_config.lifecycle_path.parent.mkdir(mode=0o700)
            rollback_config.lifecycle_path.parent.chmod(0o500)
            _expect_code(
                "CANARY_MATERIALIZATION_OUTPUT_ROLLBACK_FAILED",
                lambda: materializer.prepare_live_authority(rollback_config),
                materializer.CanaryAuthorityMaterializationError,
            )
            rollback_config.lifecycle_path.parent.chmod(0o700)
            assert not rollback_config.lifecycle_path.exists()
            assert not (
                rollback_config.private_root
                / "preselection_scope.restricted.json"
            ).exists()
            prior_root = root / "rollback-prior-private-root"
            prior_root.mkdir(mode=0o700)
            prior_source = (
                materializer.build_synthetic_materialization_authority_source(
                    prior_root,
                    repository=repository,
                    governing_commit=governing_commit,
                    run_id="lvef_c3_exact_five_canary_ac12bd34",
                )
            )
            prior_lifecycle_root = (
                prior_source.production_root
                / "owner_private"
                / "exact_five_canary"
                / "lifecycle_state"
            )
            prior_lifecycle_root.parent.parent.mkdir(mode=0o700)
            prior_lifecycle_root.parent.mkdir(mode=0o700)
            canary_state.initialize_state(
                root=prior_lifecycle_root,
                execution_state_path=execution_state_path,
                governing_commit=governing_commit,
                run_id=prior_source.run_id,
            )
            canary_state.transition_state(
                root=prior_lifecycle_root,
                execution_state_path=execution_state_path,
                expected_current="PRECANARY_READY",
                target_state="CANARY_AUTHORITY_PREPARED",
                governing_commit=governing_commit,
                run_id=prior_source.run_id,
                reason_code="SYNTHETIC_PRIOR_STATE",
                bindings={"preselection_authority_sha256": "a" * 64},
            )
            prior_lifecycle_path = (
                prior_lifecycle_root / "canary_state.restricted.json"
            )
            prior_lifecycle_bytes = prior_lifecycle_path.read_bytes()
            prior_config = materializer.discover_live_materialization_config(
                repository=repository,
                execution_state_path=execution_state_path,
                authority_source=prior_source,
            )
            _expect_code(
                "CANARY_MATERIALIZATION_STATE_NOT_READY",
                lambda: materializer.prepare_live_authority(prior_config),
                materializer.CanaryAuthorityMaterializationError,
            )
            assert prior_lifecycle_path.read_bytes() == prior_lifecycle_bytes

            prepared_text = _assert_main_pass(
                ["--prepare-live-authority"], runtime=runtime
            )
            assert "LIFECYCLE_STATE=CANARY_MANIFEST_SEALED" in prepared_text
            _assert_no_effects(effects)
            lifecycle = canary_state.load_state(
                root=lifecycle_root,
                execution_state_path=execution_state_path,
                expected_governing_commit=governing_commit,
                expected_run_id=RUN_ID,
            )
            _assert_materialization_result(
                type(
                    "Prepared",
                    (),
                    {
                        "preselection_identifier_fields": 0,
                        "state": lifecycle,
                        "manifest_study_count": 5,
                        "manifest_subject_count": 5,
                        "manifest_object_count": 5,
                        "manifest_byte_count": 15,
                    },
                )()
            )

            _assert_main_blocked(
                ["--prepare-live-authority"],
                "CANARY_MATERIALIZATION_OUTPUT_COLLISION",
                runtime=runtime,
            )
            _assert_no_effects(effects)
            normalized = _assert_packet_tamper_failures(
                authority_path=authority_path,
                authority_paths=authority_paths,
                governing_commit=governing_commit,
            )
            assert normalized["hard_scope"]["studies"] == 5

            preflight_runtime = canary.CanaryControlRuntime(
                repository_root=repository,
                execution_state_path=execution_state_path,
                orchestration_contract_path=repository
                / "configs/lvef_c3_orchestration_v2.yaml",
                scheduler_plan_path=repository
                / "configs/lvef_c3_canary_scheduler_plan_v1.json",
                synthetic_external_effects=True,
            )
            preflight_text = _assert_main_pass(
                ["--preflight-only"], runtime=preflight_runtime
            )
            assert "SYNTHETIC_QSUB_ADAPTER_CALLS=5" in preflight_text
            assert "QSUB_SUBMISSIONS=0" in preflight_text
            _assert_no_effects(effects)

            execute_text = _assert_main_pass(["--execute"], runtime=runtime)
            assert "FROZEN_SCHEDULER_SUBMISSIONS=5" in execute_text
            assert len(submitted) == 5
            assert all(command[0] == str(source.qsub_path) for command in submitted)
            assert all(command[-4] == governing_commit for command in submitted)
            assert all(command[-3] == RUN_ID for command in submitted)
            assert all(
                command[-2] == normalized["stage_launcher"]["file_sha256"]
                for command in submitted
            )
            assert all(
                command[-1] == normalized["stage_worker"]["file_sha256"]
                for command in submitted
            )
            _assert_no_effects(effects)

        latest_dispatch_path = sorted(
            (Path(normalized["output_root"]) / "scheduler_claims").glob(
                "dispatch_ledger_*.restricted.json"
            )
        )[-1]
        latest_dispatch = json.loads(latest_dispatch_path.read_text())
        dispatch.validate_dispatch_ledger(latest_dispatch)
        assert latest_dispatch["status"] == "DISPATCHED_FROZEN_DAG"
        assert latest_dispatch["submission_count"] == 5
        assert [row["job_id"] for row in latest_dispatch["stages"]] == [
            str(92_001 + index) for index in range(5)
        ]
        for index, stage_id in enumerate(stage_worker.STAGE_IDS):
            stage_worker._require_bound_scheduler_job(
                latest_dispatch,
                stage_id=stage_id,
                scheduler_job_identity=str(92_001 + index),
            )
        for invalid_job in ("99999", "not-a-job"):
            _expect_code(
                "CANARY_STAGE_SCHEDULER_JOB_BINDING_INVALID",
                lambda invalid_job=invalid_job: stage_worker._require_bound_scheduler_job(
                    latest_dispatch,
                    stage_id="DOWNLOAD",
                    scheduler_job_identity=invalid_job,
                ),
                stage_worker.CanaryStageWorkerError,
            )

        # Dispatch completion is deliberately nonterminal. Only the real final
        # worker may publish receipt-bound terminal PASS, and no scientific
        # worker or live scheduler was invoked by this control-plane fixture.
        executing = canary_state.load_state(
            root=lifecycle_root,
            execution_state_path=execution_state_path,
            expected_governing_commit=governing_commit,
            expected_run_id=RUN_ID,
        )
        assert executing["current_state"] == "CANARY_EXECUTING"
        assert executing["transition_count"] == 3
        # These production components were never mocked or substituted; the
        # control-plane path proved them reachable by identity but did not run
        # any scientific worker in this synthetic acceptance.
        assert (
            canary.PRODUCTION_FUNCTIONS["dicom_stage"]
            is production_stages.run_production_dicom_extraction
        )
        assert (
            canary.PRODUCTION_FUNCTIONS["preservation"]
            is preservation.preserve_batch
        )
        assert (
            canary.PRODUCTION_FUNCTIONS["canary_finalization"]
            is canary.finalizer.finalize_canary_preservation_receipt
        )
        tracked = canary.execution_state.load_execution_state(
            execution_state_path
        )
        assert tracked.logical_execution_attempt == 4
        assert tracked.attempt_004_execution_count == 1
        assert tracked.next_unused_execution_attempt == 5
        assert tracked.attempt_005_exists is False
        assert tracked.production_attempt_006_exists is False
        assert not any("attempt_005" in str(path) for path in root.rglob("*"))
        assert not any("attempt_006" in str(path) for path in root.rglob("*"))
